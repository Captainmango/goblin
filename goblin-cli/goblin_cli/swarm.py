from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import TYPE_CHECKING, Dict

from openhands.sdk import Agent
from openhands.sdk.conversation import get_agent_final_response
from goblin_cli.conversation import run_conversation
from goblin_cli.cost_control import CircuitBreaker, LimitExceeded
from goblin_cli.enums import AgentStatus, CleanupPolicy, SwarmState, TaskStatus
from goblin_cli.prompts import BASE_SYSTEM_PROMPT, SWARM_AGENT_PROMPT, SWARM_COORDINATOR_PROMPT
from goblin_cli.phase import PhaseProtocol, _extract_json
from goblin_cli.swarm_db import SwarmDB
from goblin_cli.utils.env import resolve_env_vars
from goblin_cli.utils.llm import build_llm
from goblin_cli.utils.tool_map import resolve_tools

if TYPE_CHECKING:
    from goblin_cli.blueprint import V1Blueprint

logger = logging.getLogger(__name__)


class SwarmPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.depends_on: list[str] | None = raw.get("depends_on")

        swarm = raw["swarm"]
        self._swarm_config: dict = swarm
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
        self._db_config: dict | None = None
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

        db_path = os.path.join(self._workspace, "swarm.db")
        swarm_config = self._swarm_config

        backend = swarm_config.get("backend") or os.environ.get("GOBLIN_BACKEND")
        if backend == "sqlite":
            self._db_config = {"db_path": db_path, "backend": "sqlite"}
        else:
            turso_block = swarm_config.get("turso")
            if turso_block is not None:
                turso_block = resolve_env_vars(turso_block)
                turso_block = self._blueprint._interpolate_strings(turso_block, context)

            if turso_block or os.environ.get("GOBLIN_TURSO_URL"):
                self._db_config = {"db_path": db_path, "turso": turso_block} if turso_block else {"db_path": db_path}
            else:
                self._db_config = {"db_path": db_path, "turso_url": db_path, "turso_mode": "local"}

        self._db = SwarmDB.from_config(self._db_config)
        self._db.init_schema()

        for agent_cfg in self.agents_config:
            self._db.register_agent(agent_cfg["name"], agent_cfg.get("roles"))

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

        assert self._db_config is not None
        db_config = self._db_config
        coordinator_model = self.coordinator_config.get("model") or self._blueprint._resolve_model(self)
        llm = build_llm(model=coordinator_model)
        agent = Agent(
            llm=llm,
            tools=resolve_tools(coordinator_tools, tool_params={"SwarmTasks": {"db_config": db_config}}),
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
            if "SwarmAgent" not in agent_tools:
                agent_tools.append("SwarmAgent")
            if "Bash" not in agent_tools:
                agent_tools.append("Bash")

            original_hooks = self.hooks_config
            if "hooks" in agent_cfg:
                self.hooks_config = agent_cfg["hooks"]
            hook_config = self._blueprint._build_hooks(self)
            self.hooks_config = original_hooks

            mcp_config = self._blueprint._build_mcp_servers(self, context)

            if "mcp_servers" in agent_cfg:
                resolved = resolve_env_vars(agent_cfg["mcp_servers"])
                interpolated = self._blueprint._interpolate_strings(resolved, context)
                existing = mcp_config.get("mcpServers", {})
                existing.update(interpolated)
                mcp_config["mcpServers"] = existing

            assert self._db_config is not None
            db_config = self._db_config
            agent_model = agent_cfg.get("model") or self._blueprint._resolve_model(self)
            llm = build_llm(model=agent_model)
            agent = Agent(
                llm=llm,
                tools=resolve_tools(
                    agent_tools,
                    tool_params={"SwarmAgent": {"db_config": db_config, "agent_name": name}},
                ),
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
