from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import textwrap
from typing import TYPE_CHECKING, Dict

from openhands.sdk import Agent
from openhands.sdk.conversation import get_agent_final_response
from goblin.conversation import run_conversation
from goblin.cost_control import CircuitBreaker, LimitExceeded
from goblin.enums import AgentStatus, CleanupPolicy, SwarmState, TaskStatus
from goblin.prompts import BASE_SYSTEM_PROMPT, SWARM_AGENT_PROMPT, SWARM_COORDINATOR_PROMPT
from goblin.phase import PhaseProtocol, _extract_json
from goblin.swarm_db import SwarmDB, get_write_validation_rules
from goblin.utils.llm import build_llm
from goblin.utils.tool_map import resolve_tools

if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint

logger = logging.getLogger(__name__)


_SWARMDB_CLI_SCRIPT_BODY = textwrap.dedent("""\
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


def _build_swarmdb_cli_script() -> str:
    """Render the swarmdb CLI script, embedding the write-validation rules
    from goblin.swarm_db so there is exactly one place those rules are
    hand-written. The rendered script is still fully self-contained
    (stdlib-only) because it may run inside a remote/sandboxed workspace
    where the goblin package is not installed.
    """
    allowed_tables, write_pattern, table_pattern = get_write_validation_rules()
    preamble = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, os, re, sqlite3, sys

        ALLOWED_WRITE_TABLES = {set(allowed_tables)!r}
        WRITE_RE = re.compile({write_pattern!r}, re.IGNORECASE)
        TABLE_RE = re.compile({table_pattern!r}, re.IGNORECASE)

    """)
    return preamble + _SWARMDB_CLI_SCRIPT_BODY


_SWARMDB_CLI_SCRIPT = _build_swarmdb_cli_script()


class SwarmPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.depends_on: list[str] | None = raw.get("depends_on")

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
            self._db.register_agent(agent_cfg["name"], agent_cfg.get("roles"))

        script_path = os.path.join(self._workspace, "swarmdb")
        with open(script_path, "w") as f:
            f.write(_SWARMDB_CLI_SCRIPT)
        os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        self._db.transition(SwarmState.COORDINATOR)

    def _build_agent_roster(self) -> str:
        lines = []
        for agent_cfg in self.agents_config:
            first_line = agent_cfg["prompt"].strip().split("\n")[0]
            roles = agent_cfg.get("roles")
            roles_str = f" (roles: {', '.join(roles)})" if roles else ""
            lines.append(f"- {agent_cfg['name']}{roles_str}: {first_line}")
        return "\n".join(lines)

    def _task_matches_agent(self, task: dict, agent_cfg: dict) -> bool:
        assigned_to = task.get("assigned_to")
        if assigned_to:
            return assigned_to == agent_cfg["name"]

        required_role = task.get("required_role")
        if required_role:
            return required_role in set(agent_cfg.get("roles", []))

        return True

    def _get_agents_to_run(self) -> list[dict]:
        assert self._db is not None
        pending_tasks = self._db.get_pending_tasks()

        if not pending_tasks:
            return self.agents_config

        return [
            agent_cfg
            for agent_cfg in self.agents_config
            if any(self._task_matches_agent(task, agent_cfg) for task in pending_tasks)
        ]

    def _coordinator_allowed_tools(self) -> list[str]:
        """The coordinator's tool list, always including SwarmTasks and never Bash.

        The coordinator only ever gets the typed SwarmTasks tool (create_task/
        list_tasks) -- never Bash -- so it has no way to write files or forge
        DB rows for tasks it didn't actually delegate.
        """
        tools = list(self.coordinator_config.get("allowed_tools", []))
        if "SwarmTasks" not in tools:
            tools.append("SwarmTasks")
        return tools

    def _render_task_board(self) -> str:
        """A snapshot of the current task board for the coordinator's prompt.

        Each coordinator invocation is a fresh, stateless conversation, so
        without this the coordinator cannot see tasks it created in earlier
        rounds -- it would re-plan from the goal every round, creating
        duplicate tasks and never recognising that the work is already done.
        """
        assert self._db is not None
        tasks = self._db.get_tasks()
        if not tasks:
            return "### Current Task Board\nThe task board is empty -- no tasks have been created yet."

        lines = ["### Current Task Board"]
        for t in tasks:
            if t.get("assigned_to"):
                target = t["assigned_to"]
            elif t.get("required_role"):
                target = f"role:{t['required_role']}"
            else:
                target = "unassigned"
            lines.append(f"- #{t['id']} [{t['status']}] {t['title']} ({target})")
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
        prompt += "\n\n" + self._render_task_board()

        coordinator_tools = self._coordinator_allowed_tools()

        mcp_config = self._blueprint._build_mcp_servers(self, context)

        db_path = os.path.join(self._workspace, "swarm.db")
        coordinator_model = self.coordinator_config.get("model") or self._blueprint._resolve_model(self)
        llm = build_llm(model=coordinator_model)
        agent = Agent(
            llm=llm,
            tools=resolve_tools(coordinator_tools, tool_params={"SwarmTasks": {"db_path": db_path}}),
            mcp_config=mcp_config,
            system_prompt=system_prompt,
            critic=None,
        )
        workspace = self._blueprint._get_workspace(context)

        conversation = await run_conversation(
            agent,
            workspace,
            prompt,
            hook_config=self._blueprint._build_hooks(self),
            max_iterations=self.coordinator_config.get("max_iterations", 50),
            max_cost=self.coordinator_config.get("max_cost"),
            secrets={"SWARMDB_PATH": db_path},
        )
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

            conversation = await run_conversation(
                agent,
                workspace,
                prompt,
                hook_config=hook_config,
                max_iterations=agent_cfg.get("max_iterations", self.max_iterations or 100),
                max_cost=agent_cfg.get("max_cost"),
                secrets={"SWARMDB_PATH": db_path},
            )
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
        agents_to_run = self._get_agents_to_run()
        async with asyncio.TaskGroup() as tg:
            tasks = []
            for agent_cfg in agents_to_run:
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

        should_cleanup = self.cleanup == CleanupPolicy.ALWAYS or (self.cleanup == CleanupPolicy.ON_SUCCESS and success)
        if should_cleanup:
            shutil.rmtree(self._workspace, ignore_errors=True)

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
                        if result.get("done") and not self._db.has_outstanding_tasks():
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
