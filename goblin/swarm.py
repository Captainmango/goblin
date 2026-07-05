from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import textwrap
from typing import TYPE_CHECKING, Dict

from openhands.sdk import Agent, LocalConversation, RemoteConversation, RemoteWorkspace
from openhands.sdk.conversation import get_agent_final_response
from goblin.cost_control import CircuitBreaker, LimitExceeded
from goblin.enums import AgentStatus, CleanupPolicy, SwarmState, TaskStatus
from goblin.prompts import BASE_SYSTEM_PROMPT, SWARM_AGENT_PROMPT, SWARM_COORDINATOR_PROMPT
from goblin.phase import PhaseProtocol, _extract_json
from goblin.swarm_db import SwarmDB
from goblin.utils.llm import build_llm
from goblin.utils.tool_map import resolve_tools

if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint

logger = logging.getLogger(__name__)


_SWARMDB_CLI_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, re, sqlite3, sys

    ALLOWED_WRITE_TABLES = {"tasks", "artifacts", "signals"}
    WRITE_RE = re.compile(r"^\\s*(INSERT|UPDATE|DELETE)\\b", re.IGNORECASE)
    TABLE_RE = re.compile(r"(?:INSERT\\s+INTO|UPDATE|DELETE\\s+FROM)\\s+(\\w+)", re.IGNORECASE)

    def main():
        if len(sys.argv) < 3:
            print("Usage: swarmdb <query|execute> <sql>", file=sys.stderr)
            sys.exit(1)

        db_path = os.environ.get("SWARMDB_PATH")
        if not db_path:
            print("Error: SWARMDB_PATH not set", file=sys.stderr)
            sys.exit(1)

        action = sys.argv[1]
        sql = sys.argv[2]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        try:
            if action == "query":
                if not sql.strip().upper().startswith("SELECT"):
                    print("Error: query only accepts SELECT statements", file=sys.stderr)
                    sys.exit(1)
                cursor = conn.execute(sql)
                rows = [dict(row) for row in cursor.fetchall()]
                print(json.dumps(rows, default=str))
            elif action == "execute":
                stripped = sql.strip()
                if not WRITE_RE.match(stripped):
                    print("Error: execute only accepts INSERT, UPDATE, or DELETE", file=sys.stderr)
                    sys.exit(1)
                match = TABLE_RE.search(stripped)
                if not match:
                    print("Error: could not determine target table", file=sys.stderr)
                    sys.exit(1)
                table = match.group(1).lower()
                if table not in ALLOWED_WRITE_TABLES:
                    print(f"Error: cannot write to '{table}'", file=sys.stderr)
                    sys.exit(1)
                cursor = conn.execute(stripped)
                conn.commit()
                print(f"OK, {cursor.rowcount} row(s) affected")
            else:
                print(f"Error: unknown action '{action}'", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    main()
""")


class SwarmPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")

        swarm = raw["swarm"]
        self.goal: str = swarm["goal"]
        self.workspace_template: str = swarm.get("workspace", ".goblin/swarms/{step_id}")
        self.coordinator_config: dict = swarm["coordinator"]
        self.agents_config: list[dict] = swarm["agents"]
        self.max_rounds: int = swarm.get("max_rounds", 5)
        self.max_iterations: int = swarm.get("max_iterations", 100)
        self.max_tokens: int | None = swarm.get("max_tokens")
        self.max_cost: float | None = swarm.get("max_cost")
        self.timeout: int | None = swarm.get("timeout")
        self.cleanup: CleanupPolicy = CleanupPolicy(swarm.get("cleanup", "on_success"))
        self.hooks_config: dict = raw.get("hooks", {})
        self.mcp_servers_config: dict | None = raw.get("mcp_servers")
        self.model: str | None = raw.get("model")

        self._blueprint = blueprint
        self._db: SwarmDB | None = None
        self._workspace: str = ""

    def _setup(self, context: dict):
        workspace = self.workspace_template.format(**{**context, "step_id": self.id})
        if not os.path.isabs(workspace):
            base = self._blueprint.project_path or os.getcwd()
            workspace = os.path.join(base, workspace)
        self._workspace = os.path.abspath(workspace)

        os.makedirs(os.path.join(self._workspace, "shared"), exist_ok=True)
        for agent_cfg in self.agents_config:
            os.makedirs(os.path.join(self._workspace, "agents", agent_cfg["name"]), exist_ok=True)

        self._db = SwarmDB(os.path.join(self._workspace, "swarm.db"))
        self._db.init_schema()

        for agent_cfg in self.agents_config:
            self._db.register_agent(agent_cfg["name"])

        script_path = os.path.join(self._workspace, "swarmdb")
        with open(script_path, "w") as f:
            f.write(_SWARMDB_CLI_SCRIPT)
        os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        self._db.transition(SwarmState.COORDINATOR)

    def _build_agent_roster(self) -> str:
        lines = []
        for agent_cfg in self.agents_config:
            first_line = agent_cfg["prompt"].strip().split("\n")[0]
            lines.append(f"- {agent_cfg['name']}: {first_line}")
        return "\n".join(lines)

    async def _run_coordinator(self, context: dict, breaker: CircuitBreaker) -> dict:
        assert self._db is not None
        agent_roster = self._build_agent_roster()

        system_prompt = SWARM_COORDINATOR_PROMPT.format(
            goal=self.goal,
            agent_roster=agent_roster,
            workspace=self._workspace,
        )
        if self._blueprint.project_refs:
            system_prompt += "\n\n" + self._blueprint.project_refs

        prompt = self.coordinator_config["prompt"].format(**context)

        coordinator_tools = list(self.coordinator_config.get("allowed_tools", []))
        if "Bash" not in coordinator_tools:
            coordinator_tools.append("Bash")

        mcp_config = self._blueprint._build_mcp_servers(self, context)

        db_path = os.path.join(self._workspace, "swarm.db")
        coordinator_model = self.coordinator_config.get("model") or self._blueprint._resolve_model(self)
        llm = build_llm(model=coordinator_model)
        agent = Agent(
            llm=llm,
            tools=resolve_tools(coordinator_tools),
            mcp_config=mcp_config,
            system_prompt=system_prompt,
            critic=None,
        )
        workspace = self._blueprint._get_workspace(context)

        if isinstance(workspace, RemoteWorkspace):
            conversation = RemoteConversation(
                agent=agent,
                workspace=workspace,
                hook_config=self._blueprint._build_hooks(self),
                max_iteration_per_run=self.coordinator_config.get("max_iterations", 50),
                max_budget_per_run=self.coordinator_config.get("max_cost"),
                secrets={"SWARMDB_PATH": db_path},
            )
            conversation.send_message(prompt)
            await asyncio.to_thread(conversation.run)
            breaker.absorb(conversation)
        else:
            conversation = LocalConversation(
                agent=agent,
                workspace=workspace,
                hook_config=self._blueprint._build_hooks(self),
                max_iteration_per_run=self.coordinator_config.get("max_iterations", 50),
                max_budget_per_run=self.coordinator_config.get("max_cost"),
                secrets={"SWARMDB_PATH": db_path},
            )
            conversation.send_message(prompt)
            await conversation.arun()
            breaker.absorb(conversation)

        text = get_agent_final_response(conversation.state.events)
        if not text:
            return {"done": False, "message": ["No response from coordinator"]}

        try:
            return _extract_json(text)
        except (json.JSONDecodeError, ValueError):
            return {"done": False, "message": [text]}

    async def _run_single_agent(self, agent_cfg: dict, context: dict, breaker: CircuitBreaker):
        assert self._db is not None
        name = agent_cfg["name"]
        self._db.update_agent_status(name, AgentStatus.RUNNING)

        try:
            agent_roster = self._build_agent_roster()
            system_prompt = BASE_SYSTEM_PROMPT + SWARM_AGENT_PROMPT.format(
                agent_name=name,
                goal=self.goal,
                workspace=self._workspace,
                agent_roster=agent_roster,
            )
            if self._blueprint.project_refs:
                system_prompt += "\n\n" + self._blueprint.project_refs

            prompt = agent_cfg["prompt"].format(**context)

            agent_tools = list(agent_cfg.get("allowed_tools", []))
            if "Bash" not in agent_tools:
                agent_tools.append("Bash")

            original_hooks = self.hooks_config
            if "hooks" in agent_cfg:
                self.hooks_config = agent_cfg["hooks"]
            hook_config = self._blueprint._build_hooks(self)
            self.hooks_config = original_hooks

            mcp_config = self._blueprint._build_mcp_servers(self, context)

            if "mcp_servers" in agent_cfg:
                from goblin.utils.env import resolve_env_vars

                resolved = resolve_env_vars(agent_cfg["mcp_servers"])
                interpolated = self._blueprint._interpolate_strings(resolved, context)
                existing = mcp_config.get("mcpServers", {})
                existing.update(interpolated)
                mcp_config["mcpServers"] = existing

            db_path = os.path.join(self._workspace, "swarm.db")
            agent_model = agent_cfg.get("model") or self._blueprint._resolve_model(self)
            llm = build_llm(model=agent_model)
            agent = Agent(
                llm=llm,
                tools=resolve_tools(agent_tools),
                mcp_config=mcp_config,
                system_prompt=system_prompt,
                critic=None,
            )
            workspace = self._blueprint._get_workspace(context)

            if isinstance(workspace, RemoteWorkspace):
                conversation = RemoteConversation(
                    agent=agent,
                    workspace=workspace,
                    hook_config=hook_config,
                    max_iteration_per_run=agent_cfg.get("max_iterations", self.max_iterations or 100),
                    max_budget_per_run=agent_cfg.get("max_cost"),
                    secrets={"SWARMDB_PATH": db_path},
                )
                conversation.send_message(prompt)
                await asyncio.to_thread(conversation.run)
                breaker.absorb(conversation)
            else:
                conversation = LocalConversation(
                    agent=agent,
                    workspace=workspace,
                    hook_config=hook_config,
                    max_iteration_per_run=agent_cfg.get("max_iterations", self.max_iterations or 100),
                    max_budget_per_run=agent_cfg.get("max_cost"),
                    secrets={"SWARMDB_PATH": db_path},
                )
                conversation.send_message(prompt)
                await conversation.arun()
                breaker.absorb(conversation)

            self._db.update_agent_status(name, AgentStatus.DONE)
            return get_agent_final_response(conversation.state.events) or ""

        except LimitExceeded:
            raise
        except Exception as e:
            self._db.update_agent_status(name, AgentStatus.FAILED)
            logger.error("Swarm agent failed", extra={"event": "swarm.agent_failed", "agent": name, "error": str(e)})
            return ""

    async def _run_agents(self, context: dict, breaker: CircuitBreaker):
        async with asyncio.TaskGroup() as tg:
            tasks = []
            for agent_cfg in self.agents_config:
                t = tg.create_task(self._run_single_agent(agent_cfg, context, breaker))
                tasks.append(t)
        return [t.result() for t in tasks]

    def _teardown(self, spend=None) -> dict:
        assert self._db is not None
        tasks = self._db.get_tasks()
        artifacts = self._db.get_artifacts()
        success = all(t.get("status") == TaskStatus.DONE for t in tasks) if tasks else True

        messages = []
        for t in tasks:
            status = t.get("status", "unknown")
            title = t.get("title", "untitled")
            messages.append(f"[{status}] {title}")

        result: Dict = {
            "success": success,
            "message": messages if messages else ["Swarm completed"],
            "tasks": tasks,
            "artifacts": artifacts,
            "spend": spend,
        }

        self._db.close()

        match self.cleanup:
            case CleanupPolicy.ON_SUCCESS if success:
                shutil.rmtree(self._workspace, ignore_errors=True)
            case CleanupPolicy.ALWAYS:
                shutil.rmtree(self._workspace, ignore_errors=True)
            case _:
                pass

        return result

    async def run(self, context: dict) -> Dict:
        self._setup(context)
        assert self._db is not None

        breaker = CircuitBreaker(
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )

        deadline = None
        if self.timeout:
            deadline = asyncio.get_running_loop().time() + self.timeout

        try:
            while True:
                state = self._db.get_state()

                if deadline is not None and asyncio.get_running_loop().time() > deadline:
                    self._db.transition(SwarmState.TEARDOWN)
                    continue

                match state:
                    case SwarmState.SETUP:
                        self._db.transition(SwarmState.COORDINATOR)

                    case SwarmState.COORDINATOR:
                        result = await self._run_coordinator(context, breaker)
                        if result.get("done"):
                            self._db.transition(SwarmState.TEARDOWN)
                        else:
                            self._db.transition(SwarmState.AGENTS_RUNNING)

                    case SwarmState.AGENTS_RUNNING:
                        await self._run_agents(context, breaker)
                        self._db.transition(SwarmState.EVALUATING)

                    case SwarmState.EVALUATING:
                        round_num = self._db.increment_round()
                        if round_num >= self.max_rounds:
                            self._db.transition(SwarmState.TEARDOWN)
                        else:
                            self._db.transition(SwarmState.COORDINATOR)

                    case SwarmState.PAUSED:
                        return {"success": False, "message": ["Swarm paused"], "paused": True, "spend": breaker.tracker}

                    case SwarmState.TEARDOWN:
                        return self._teardown(spend=breaker.tracker)
        except LimitExceeded as exc:
            self._db.transition(SwarmState.TEARDOWN)
            result = self._teardown(spend=exc.tracker)
            result["success"] = False
            result["message"].append(f"Circuit breaker: {exc.reason} limit exceeded")
            result["spend"] = exc.tracker
            return result
