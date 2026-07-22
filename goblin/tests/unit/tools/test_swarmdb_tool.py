"""Tests for the typed swarm_tasks tool given to the coordinator.

These exercise the ToolDefinition/ToolExecutor pair directly against a real
SwarmDB (tmp_path-backed sqlite file) -- no OpenHands SDK conversation/agent
machinery is started, per .claude/rules/testing.md.
"""

from __future__ import annotations

from goblin.swarm_db import SwarmDB
from goblin.tools.swarmdb_tool import (
    SwarmTasksAction,
    SwarmTasksExecutor,
    SwarmTasksObservation,
    SwarmTasksTool,
)


def _make_db_config(tmp_path) -> dict:
    db_path = str(tmp_path / "swarm.db")
    db = SwarmDB.from_config({"db_path": db_path})
    db.init_schema()
    db.close()
    return {"db_path": db_path}


class TestSwarmTasksExecutorCreateTask:
    def test_create_task_inserts_pending_row(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmTasksExecutor(db_config)

        obs = executor(SwarmTasksAction(command="create_task", title="Implement feature"))

        assert isinstance(obs, SwarmTasksObservation)
        assert obs.is_error is False
        assert obs.task_id is not None
        executor.close()

        db = SwarmDB.from_config(db_config)
        rows = db.get_tasks()
        assert len(rows) == 1
        assert rows[0]["title"] == "Implement feature"
        assert rows[0]["status"] == "pending"
        db.close()

    def test_create_task_with_assignment_and_role(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmTasksExecutor(db_config)

        executor(
            SwarmTasksAction(
                command="create_task",
                title="Write tests",
                description="cover the new endpoint",
                assigned_to="tester",
                required_role="qa",
            )
        )
        executor.close()

        db = SwarmDB.from_config(db_config)
        rows = db.get_tasks()
        assert rows[0]["assigned_to"] == "tester"
        assert rows[0]["required_role"] == "qa"
        assert rows[0]["description"] == "cover the new endpoint"
        db.close()

    def test_create_task_without_title_errors(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmTasksExecutor(db_config)

        obs = executor(SwarmTasksAction(command="create_task"))

        assert obs.is_error is True
        executor.close()

        db = SwarmDB.from_config(db_config)
        assert db.get_tasks() == []
        db.close()


class TestSwarmTasksExecutorListTasks:
    def test_list_tasks_empty(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmTasksExecutor(db_config)

        obs = executor(SwarmTasksAction(command="list_tasks"))

        assert obs.tasks == []
        executor.close()

    def test_list_tasks_reflects_worker_updates(self, tmp_path):
        """The coordinator's tool can observe status changes made by workers
        through the typed SwarmAgent tool, but cannot cause them."""
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        task_id = db.create_task("Ship it")
        db.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
        db.close()

        executor = SwarmTasksExecutor(db_config)
        obs = executor(SwarmTasksAction(command="list_tasks"))

        assert len(obs.tasks) == 1
        assert obs.tasks[0]["status"] == "done"
        executor.close()


class TestSwarmTasksExecutorHasNoWriteCapabilityBeyondCreate:
    def test_executor_exposes_no_update_delete_or_execute_path(self):
        """The executor's only way to touch the DB is create_task() (INSERT)
        and get_tasks() (SELECT) -- there is no branch in __call__ that can
        reach SwarmDB.execute()/transition()/update_agent_status(), so the
        coordinator cannot mark its own tasks done or forge state."""
        import inspect

        source = inspect.getsource(SwarmTasksExecutor.__call__)
        assert "create_task" in source
        assert "get_tasks" in source
        assert ".execute(" not in source
        assert ".transition(" not in source
        assert ".update_agent_status(" not in source

    def test_action_schema_has_no_status_or_id_field(self):
        """Without a `status` or task-`id` field on the action, an agent has
        no way to even express 'mark task N done' through this tool."""
        fields = set(SwarmTasksAction.model_fields)
        assert "status" not in fields
        assert "id" not in fields


class TestSwarmTasksToolCreate:
    def test_create_returns_tool_with_executor(self, tmp_path):
        db_config = _make_db_config(tmp_path)

        tools = SwarmTasksTool.create(conv_state=None, db_config=db_config)

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "swarm_tasks"
        assert isinstance(tool.executor, SwarmTasksExecutor)
        tool.executor.close()

    def test_created_tool_is_callable_end_to_end(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        tool = SwarmTasksTool.create(conv_state=None, db_config=db_config)[0]

        obs = tool(SwarmTasksAction(command="create_task", title="End to end"))

        assert obs.task_id is not None
        tool.executor.close()

        db = SwarmDB.from_config(db_config)
        assert db.get_tasks()[0]["title"] == "End to end"
        db.close()
