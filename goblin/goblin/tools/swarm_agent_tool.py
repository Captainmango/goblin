"""Typed swarm-database tool for worker agents.

Replaces the generated `swarmdb` CLI script that agents previously invoked
through Bash. All database mutations go through high-level commands
(`claim_task`, `complete_task`, `create_artifact`, `send_signal`) so agents
never need raw `execute()` access. Read-only ad-hoc queries are still
supported via `query`, but only SELECT statements are accepted by the
backend.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

from goblin.swarm_db import SwarmDB

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class SwarmAgentAction(Action):
    """Schema for agent-side swarm-database operations."""

    command: Literal[
        "query",
        "claim_task",
        "complete_task",
        "create_artifact",
        "send_signal",
        "check_signals",
    ] = Field(description="The swarm-database operation to perform.")
    sql: str | None = Field(default=None, description="Required for `query`: a SELECT statement.")
    task_id: int | None = Field(default=None, description="Required for `claim_task` and `complete_task`: the task id.")
    result: str | None = Field(
        default=None, description="Required for `complete_task`: a short summary of the outcome."
    )
    name: str | None = Field(default=None, description="Required for `create_artifact`: artifact name.")
    path: str | None = Field(
        default=None, description="Required for `create_artifact`: filesystem path of the artifact."
    )
    content: str | None = Field(default=None, description="Optional for `create_artifact`: inline content or summary.")
    to_agent: str | None = Field(
        default=None,
        description="Required for `send_signal`: recipient agent name. Use NULL/broadcast via check_signals.",
    )
    signal_type: str | None = Field(
        default=None, description="Required for `send_signal`: the type of signal being sent."
    )
    payload: str | None = Field(default=None, description="Optional for `send_signal`: JSON or text payload.")


class SwarmAgentObservation(Observation):
    """Result of an agent-side swarm-database operation."""

    rows: list[dict] | None = Field(default=None, description="Rows returned by `query` or `check_signals`.")
    rowcount: int | None = Field(default=None, description="Number of rows affected by a write command.")
    task_id: int | None = Field(default=None, description="The id of the task claimed or completed.")
    signal_id: int | None = Field(default=None, description="The id of the signal sent.")


TOOL_DESCRIPTION = """Interact with the swarm's shared coordination database.
* `query`: run a read-only SELECT statement against the database. Useful for inspecting tasks, artifacts, or signals.
* `claim_task`: mark a pending task as `in_progress` and assign it to yourself. Only succeeds if the task is still pending.
* `complete_task`: mark a task you claimed as `done` and record a result summary.
* `create_artifact`: register a deliverable (file, document, etc.) in the shared artifacts table.
* `send_signal`: send a message/signal to another agent or broadcast it.
* `check_signals`: read signals addressed to you or broadcast to all agents.

You cannot mutate `swarm_state` or `agent_state`. Use `query` for reads only; all writes must go through the high-level commands above.
"""  # noqa: E501


class SwarmAgentExecutor(ToolExecutor[SwarmAgentAction, SwarmAgentObservation]):
    """Opens its own SwarmDB connection from `db_config`.

    `agent_name` is injected by SwarmPhase so claim/complete operations are
    always attributed to the caller. Like the coordinator's tool, the executor
    is created from serialisable params (`db_config` and `agent_name`) so it
    survives OpenHands' conversation state snapshot.
    """

    def __init__(self, db_config: dict, agent_name: str):
        self._db = SwarmDB.from_config(db_config)
        self._agent_name = agent_name

    def close(self) -> None:
        self._db.close()

    def __call__(
        self,
        action: SwarmAgentAction,
        conversation: "LocalConversation | None" = None,
    ) -> SwarmAgentObservation:
        try:
            return self._handle(action)
        except Exception as e:
            return SwarmAgentObservation.from_text(text=f"Error: {e}", is_error=True)

    def _handle(self, action: SwarmAgentAction) -> SwarmAgentObservation:
        if action.command == "query":
            if not action.sql:
                return SwarmAgentObservation.from_text(text="Error: `sql` is required for query", is_error=True)
            rows = self._db.query(action.sql)
            return SwarmAgentObservation.from_text(text=f"{len(rows)} row(s)", rows=rows)

        if action.command == "claim_task":
            if action.task_id is None:
                return SwarmAgentObservation.from_text(
                    text="Error: `task_id` is required for claim_task", is_error=True
                )
            rowcount = self._db.execute(
                "UPDATE tasks SET status = 'in_progress', assigned_to = ? WHERE id = ? AND status = 'pending'",
                (self._agent_name, action.task_id),
            )
            text = "Claimed" if rowcount else "Already claimed or not found"
            return SwarmAgentObservation.from_text(text=text, rowcount=rowcount, task_id=action.task_id)

        if action.command == "complete_task":
            if action.task_id is None:
                return SwarmAgentObservation.from_text(
                    text="Error: `task_id` is required for complete_task", is_error=True
                )
            rowcount = self._db.execute(
                "UPDATE tasks SET status = 'done', result = ? WHERE id = ? AND assigned_to = ?",
                (action.result, action.task_id, self._agent_name),
            )
            text = "Completed" if rowcount else "Task not found or not assigned to you"
            return SwarmAgentObservation.from_text(text=text, rowcount=rowcount, task_id=action.task_id)

        if action.command == "create_artifact":
            if not action.name:
                return SwarmAgentObservation.from_text(
                    text="Error: `name` is required for create_artifact", is_error=True
                )
            artifact_id = self._db.execute(
                "INSERT INTO artifacts (agent, name, path, content) VALUES (?, ?, ?, ?)",
                (self._agent_name, action.name, action.path, action.content),
            )
            return SwarmAgentObservation.from_text(
                text=f"Created artifact {action.name}",
                rowcount=artifact_id,
            )

        if action.command == "send_signal":
            if not action.to_agent:
                return SwarmAgentObservation.from_text(
                    text="Error: `to_agent` is required for send_signal", is_error=True
                )
            if not action.signal_type:
                return SwarmAgentObservation.from_text(
                    text="Error: `signal_type` is required for send_signal", is_error=True
                )
            signal_id = self._db.execute(
                "INSERT INTO signals (from_agent, to_agent, type, payload) VALUES (?, ?, ?, ?)",
                (self._agent_name, action.to_agent, action.signal_type, action.payload),
            )
            return SwarmAgentObservation.from_text(
                text=f"Sent signal to {action.to_agent}",
                rowcount=signal_id,
                signal_id=signal_id,
            )

        if action.command == "check_signals":
            rows = self._db.get_signals(to_agent=self._agent_name)
            return SwarmAgentObservation.from_text(text=f"{len(rows)} signal(s)", rows=rows)

        return SwarmAgentObservation.from_text(text=f"Unknown command: {action.command}", is_error=True)


class SwarmAgentTool(ToolDefinition[SwarmAgentAction, SwarmAgentObservation]):
    """Typed swarm-database tool given to worker agents instead of Bash DB access."""

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Sequence["SwarmAgentTool"]:
        db_config: dict = kwargs["db_config"]
        agent_name: str = kwargs["agent_name"]
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=SwarmAgentAction,
                observation_type=SwarmAgentObservation,
                annotations=ToolAnnotations(
                    title="swarm_agent",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=SwarmAgentExecutor(db_config, agent_name),
            )
        ]


register_tool(SwarmAgentTool.name, SwarmAgentTool)
