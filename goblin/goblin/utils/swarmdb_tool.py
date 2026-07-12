"""Typed 'create task / list tasks' tool for the swarm coordinator.

Replaces the Bash + swarmdb-CLI access the coordinator used to have. The
executor only ever calls SwarmDB.create_task() (INSERT) and SwarmDB.get_tasks()
(SELECT) -- there is no code path here to UPDATE/DELETE a task or write an
artifact, so the coordinator is physically incapable of marking its own
tasks done or forging results. Worker agents use the typed SwarmAgent tool
for that (see goblin/utils/swarm_agent_tool.py).
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


class SwarmTasksAction(Action):
    """Schema for coordinator task-board operations."""

    command: Literal["create_task", "list_tasks"] = Field(
        description="`create_task` to add a new pending task, `list_tasks` to view all tasks and their status."
    )
    title: str | None = Field(default=None, description="Required for `create_task`. A short task title.")
    description: str | None = Field(default=None, description="Optional longer description of the task.")
    assigned_to: str | None = Field(default=None, description="Optional: assign this task to a specific agent by name.")
    required_role: str | None = Field(
        default=None, description="Optional: restrict this task to agents with a matching role."
    )


class SwarmTasksObservation(Observation):
    """Result of a task-board operation."""

    task_id: int | None = Field(default=None, description="The id of the task just created.")
    tasks: list[dict] | None = Field(default=None, description="All tasks, returned by `list_tasks`.")


TOOL_DESCRIPTION = """Manage the swarm's shared task board.
* `create_task`: insert a new pending task for worker agents to claim. Optionally assign it to a specific agent (`assigned_to`) or restrict it to a role (`required_role`).
* `list_tasks`: view every task and its current status (pending, in_progress, done, failed) to check progress.

You cannot mark a task done, edit a task, or write artifacts yourself -- only the worker agent that claims a task can complete it. Do not use Bash or any other tool to modify the task board or produce deliverables directly.
"""  # noqa: E501


class SwarmTasksExecutor(ToolExecutor[SwarmTasksAction, SwarmTasksObservation]):
    """Opens its own SwarmDB connection from `db_config`.

    A live SwarmDB can't be threaded through `Tool.params` -- that dict gets
    JSON-serialized into the conversation's persisted state snapshot, and
    SwarmDB isn't serializable. `db_config` is a plain dict (e.g.
    `{"db_path": "..."}`), so it survives that round-trip; SwarmDB's WAL mode
    makes a second connection to the same file safe alongside the SwarmPhase
    orchestrator's own connection.
    """

    def __init__(self, db_config: dict):
        self._db = SwarmDB.from_config(db_config)

    def close(self) -> None:
        self._db.close()

    def __call__(
        self,
        action: SwarmTasksAction,
        conversation: "LocalConversation | None" = None,
    ) -> SwarmTasksObservation:
        if action.command == "create_task":
            if not action.title:
                return SwarmTasksObservation.from_text(text="Error: `title` is required for create_task", is_error=True)
            task_id = self._db.create_task(
                title=action.title,
                description=action.description,
                assigned_to=action.assigned_to,
                required_role=action.required_role,
            )
            return SwarmTasksObservation.from_text(text=f"Created task {task_id}: {action.title}", task_id=task_id)

        tasks = self._db.get_tasks()
        lines = [f"[{t['status']}] #{t['id']} {t['title']}" for t in tasks]
        text = "\n".join(lines) if lines else "No tasks yet."
        return SwarmTasksObservation.from_text(text=text, tasks=tasks)


class SwarmTasksTool(ToolDefinition[SwarmTasksAction, SwarmTasksObservation]):
    """Typed task-board tool given to the swarm coordinator instead of Bash."""

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Sequence["SwarmTasksTool"]:
        db_config: dict = kwargs["db_config"]
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=SwarmTasksAction,
                observation_type=SwarmTasksObservation,
                annotations=ToolAnnotations(
                    title="swarm_tasks",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=SwarmTasksExecutor(db_config),
            )
        ]


register_tool(SwarmTasksTool.name, SwarmTasksTool)
