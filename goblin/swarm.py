from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import textwrap
from typing import TYPE_CHECKING, Any, Dict

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    query,
)

from goblin.enums import AgentStatus, CleanupPolicy, SwarmState
from goblin.prompts import BASE_SYSTEM_PROMPT, SWARM_AGENT_PROMPT, SWARM_COORDINATOR_PROMPT
from goblin.step import StepProtocol, _extract_json
from goblin.swarm_db import SwarmDB

if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint


# The CLI wrapper script placed in the workspace for agents to call via Bash.
# Agents run:  swarmdb query "SELECT * FROM tasks"
#              swarmdb execute "INSERT INTO tasks ..."
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


class SwarmStep(StepProtocol):
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
        self.timeout: int | None = swarm.get("timeout")
        self.cleanup: CleanupPolicy = CleanupPolicy(swarm.get("cleanup", "on_success"))
        self.hooks_config: dict = raw.get("hooks", {})
        self.mcp_servers_config: dict | None = raw.get("mcp_servers")

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

        # Write the CLI wrapper script into the workspace
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

    def _swarmdb_env(self) -> dict[str, str]:
        """Return env vars that include the SwarmDB path so the CLI script works."""
        return {"CLAUDECODE": "", "SWARMDB_PATH": os.path.join(self._workspace, "swarm.db")}

    async def _run_coordinator(self, context: dict) -> dict:
        assert self._db is not None
        agent_roster = self._build_agent_roster()

        system_prompt = BASE_SYSTEM_PROMPT + SWARM_COORDINATOR_PROMPT.format(
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

        mcp_servers = self._blueprint._build_mcp_servers(self, context)

        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "allowed_tools": coordinator_tools,
            "hooks": self._blueprint._build_hooks(self),
            "env": self._swarmdb_env(),
        }

        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers

        if self._blueprint.project_path:
            kwargs["cwd"] = self._blueprint.project_path

        options = ClaudeAgentOptions(**kwargs)

        text_blocks: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        text_blocks.append(block.text)

        if not text_blocks:
            return {"done": False, "message": ["No response from coordinator"]}

        try:
            return _extract_json(text_blocks[-1])
        except (json.JSONDecodeError, ValueError):
            return {"done": False, "message": text_blocks}

    async def _run_single_agent(self, agent_cfg: dict, context: dict):
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

            # Build hooks: use per-agent hooks if specified, else step-level
            original_hooks = self.hooks_config
            if "hooks" in agent_cfg:
                self.hooks_config = agent_cfg["hooks"]
            hooks = self._blueprint._build_hooks(self)
            self.hooks_config = original_hooks

            mcp_servers = self._blueprint._build_mcp_servers(self, context)

            # Merge per-agent MCP servers if specified
            if "mcp_servers" in agent_cfg:
                from goblin.utils.env import resolve_env_vars

                resolved = resolve_env_vars(agent_cfg["mcp_servers"])
                interpolated = self._blueprint._interpolate_strings(resolved, context)
                mcp_servers.update(interpolated)

            kwargs: dict[str, Any] = {
                "system_prompt": system_prompt,
                "allowed_tools": agent_tools,
                "hooks": hooks,
                "max_turns": self.max_iterations,
                "env": self._swarmdb_env(),
            }

            if mcp_servers:
                kwargs["mcp_servers"] = mcp_servers

            if self._blueprint.project_path:
                kwargs["cwd"] = self._blueprint.project_path

            options = ClaudeAgentOptions(**kwargs)

            text_blocks: list[str] = []
            turns = 0
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    turns += 1
                    for block in message.content:
                        if hasattr(block, "text"):
                            text_blocks.append(block.text)

            self._db.update_agent_status(name, AgentStatus.DONE, iterations=turns)
            return text_blocks[-1] if text_blocks else ""

        except Exception as e:
            self._db.update_agent_status(name, AgentStatus.FAILED)
            print(f"Agent '{name}' failed: {e}")
            return ""

    async def _run_agents(self, context: dict):
        async with asyncio.TaskGroup() as tg:
            tasks = []
            for agent_cfg in self.agents_config:
                t = tg.create_task(self._run_single_agent(agent_cfg, context))
                tasks.append(t)
        return [t.result() for t in tasks]

    def _teardown(self) -> dict:
        assert self._db is not None
        tasks = self._db.get_tasks()
        artifacts = self._db.get_artifacts()
        success = all(t.get("status") == "done" for t in tasks) if tasks else True

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

        deadline = None
        if self.timeout:
            deadline = asyncio.get_event_loop().time() + self.timeout

        while True:
            state = self._db.get_state()

            if deadline is not None and asyncio.get_event_loop().time() > deadline:
                self._db.transition(SwarmState.TEARDOWN)
                continue

            match state:
                case SwarmState.SETUP:
                    self._db.transition(SwarmState.COORDINATOR)

                case SwarmState.COORDINATOR:
                    result = await self._run_coordinator(context)
                    if result.get("done"):
                        self._db.transition(SwarmState.TEARDOWN)
                    else:
                        self._db.transition(SwarmState.AGENTS_RUNNING)

                case SwarmState.AGENTS_RUNNING:
                    await self._run_agents(context)
                    self._db.transition(SwarmState.EVALUATING)

                case SwarmState.EVALUATING:
                    round_num = self._db.increment_round()
                    if round_num >= self.max_rounds:
                        self._db.transition(SwarmState.TEARDOWN)
                    else:
                        self._db.transition(SwarmState.COORDINATOR)

                case SwarmState.PAUSED:
                    return {"success": False, "message": ["Swarm paused"], "paused": True}

                case SwarmState.TEARDOWN:
                    return self._teardown()
