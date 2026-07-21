"""Tests for the typed swarm_agent tool given to worker agents.

These exercise the ToolDefinition/ToolExecutor pair directly against a real
SwarmDB (tmp_path-backed sqlite file) -- no OpenHands SDK conversation/agent
machinery is started, per .claude/rules/testing.md.
"""

from __future__ import annotations

from goblin.swarm_db import SwarmDB
from goblin.tools.swarm_agent_tool import (
    SwarmAgentAction,
    SwarmAgentExecutor,
    SwarmAgentObservation,
    SwarmAgentTool,
)


def _make_db_config(tmp_path) -> dict:
    db_path = str(tmp_path / "swarm.db")
    db = SwarmDB.from_config({"db_path": db_path})
    db.init_schema()
    db.close()
    return {"db_path": db_path}


class TestSwarmAgentExecutorClaimTask:
    def test_claim_task_success(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        task_id = db.create_task("Implement feature")
        db.close()

        executor = SwarmAgentExecutor(db_config, agent_name="coder")
        obs = executor(SwarmAgentAction(command="claim_task", task_id=task_id))

        assert isinstance(obs, SwarmAgentObservation)
        assert obs.is_error is False
        assert obs.rowcount == 1
        assert obs.task_id == task_id
        executor.close()

        db = SwarmDB.from_config(db_config)
        rows = db.get_tasks()
        assert rows[0]["status"] == "in_progress"
        assert rows[0]["assigned_to"] == "coder"
        db.close()

    def test_claim_task_already_claimed(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        task_id = db.create_task("Implement feature")
        db.execute(
            "UPDATE tasks SET status = 'in_progress', assigned_to = 'other_agent' WHERE id = ?",
            (task_id,),
        )
        db.close()

        executor = SwarmAgentExecutor(db_config, agent_name="coder")
        obs = executor(SwarmAgentAction(command="claim_task", task_id=task_id))

        assert obs.rowcount == 0
        assert "Already claimed" in obs.text
        executor.close()

    def test_claim_task_missing_id_errors(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="claim_task"))

        assert obs.is_error is True
        assert "task_id" in obs.text
        executor.close()


class TestSwarmAgentExecutorCompleteTask:
    def test_complete_task(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        task_id = db.create_task("Implement feature")
        db.execute(
            "UPDATE tasks SET status = 'in_progress', assigned_to = 'coder' WHERE id = ?",
            (task_id,),
        )
        db.close()

        executor = SwarmAgentExecutor(db_config, agent_name="coder")
        obs = executor(SwarmAgentAction(command="complete_task", task_id=task_id, result="Done"))

        assert obs.is_error is False
        assert obs.rowcount == 1
        assert obs.task_id == task_id
        executor.close()

        db = SwarmDB.from_config(db_config)
        rows = db.get_tasks()
        assert rows[0]["status"] == "done"
        assert rows[0]["result"] == "Done"
        db.close()

    def test_complete_task_only_if_assigned_to_self(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        task_id = db.create_task("Implement feature")
        db.execute(
            "UPDATE tasks SET status = 'in_progress', assigned_to = 'other_agent' WHERE id = ?",
            (task_id,),
        )
        db.close()

        executor = SwarmAgentExecutor(db_config, agent_name="coder")
        obs = executor(SwarmAgentAction(command="complete_task", task_id=task_id, result="Done"))

        assert obs.rowcount == 0
        assert "not assigned to you" in obs.text
        executor.close()


class TestSwarmAgentExecutorCreateArtifact:
    def test_create_artifact(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(
            SwarmAgentAction(
                command="create_artifact",
                name="main.py",
                path="shared/main.py",
                content="print('hello')",
            )
        )

        assert obs.is_error is False
        assert obs.rowcount == 1
        executor.close()

        db = SwarmDB.from_config(db_config)
        rows = db.get_artifacts()
        assert len(rows) == 1
        assert rows[0]["agent"] == "coder"
        assert rows[0]["name"] == "main.py"
        assert rows[0]["path"] == "shared/main.py"
        assert rows[0]["content"] == "print('hello')"
        db.close()

    def test_create_artifact_requires_name(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="create_artifact", path="shared/main.py"))

        assert obs.is_error is True
        assert "name" in obs.text
        executor.close()


class TestSwarmAgentExecutorSignals:
    def test_send_signal(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(
            SwarmAgentAction(
                command="send_signal",
                to_agent="tester",
                signal_type="review_request",
                payload="Please review PR #1",
            )
        )

        assert obs.is_error is False
        assert obs.rowcount == 1
        assert obs.signal_id is not None
        executor.close()

        db = SwarmDB.from_config(db_config)
        rows = db.get_signals()
        assert len(rows) == 1
        assert rows[0]["from_agent"] == "coder"
        assert rows[0]["to_agent"] == "tester"
        assert rows[0]["type"] == "review_request"
        assert rows[0]["payload"] == "Please review PR #1"
        db.close()

    def test_send_signal_requires_to_agent_and_type(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="send_signal", to_agent="tester"))
        assert obs.is_error is True
        assert "signal_type" in obs.text
        executor.close()

    def test_check_signals(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        db.execute(
            "INSERT INTO signals (from_agent, to_agent, type, payload) VALUES (?, ?, ?, ?)",
            ("coder", "tester", "ping", "hello"),
        )
        db.execute(
            "INSERT INTO signals (from_agent, to_agent, type, payload) VALUES (?, ?, ?, ?)",
            ("coder", None, "broadcast", "all hands"),
        )
        db.execute(
            "INSERT INTO signals (from_agent, to_agent, type, payload) VALUES (?, ?, ?, ?)",
            ("tester", "coder", "reply", "hi"),
        )
        db.close()

        executor = SwarmAgentExecutor(db_config, agent_name="tester")
        obs = executor(SwarmAgentAction(command="check_signals"))

        assert obs.is_error is False
        assert len(obs.rows) == 2
        assert all(r["to_agent"] in ("tester", None) for r in obs.rows)
        executor.close()


class TestSwarmAgentExecutorQuery:
    def test_query_select_only(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        db = SwarmDB.from_config(db_config)
        db.create_task("T1")
        db.close()

        executor = SwarmAgentExecutor(db_config, agent_name="coder")
        obs = executor(SwarmAgentAction(command="query", sql="SELECT * FROM tasks"))

        assert obs.is_error is False
        assert len(obs.rows) == 1
        assert obs.rows[0]["title"] == "T1"
        executor.close()

    def test_query_rejects_non_select(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="query", sql="DELETE FROM tasks"))

        assert obs.is_error is True
        assert "only accepts SELECT" in obs.text
        executor.close()

    def test_query_requires_sql(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="query"))

        assert obs.is_error is True
        assert "sql" in obs.text
        executor.close()


class TestSwarmAgentExecutorSecurity:
    def test_cannot_write_to_swarm_state_via_query(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="query", sql="UPDATE swarm_state SET state = 'teardown'"))

        assert obs.is_error is True
        executor.close()

    def test_cannot_write_to_agent_state_via_query(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        executor = SwarmAgentExecutor(db_config, agent_name="coder")

        obs = executor(SwarmAgentAction(command="query", sql="UPDATE agent_state SET status = 'done'"))

        assert obs.is_error is True
        executor.close()


class TestSwarmAgentToolCreate:
    def test_create_returns_tool_with_executor(self, tmp_path):
        db_config = _make_db_config(tmp_path)

        tools = SwarmAgentTool.create(conv_state=None, db_config=db_config, agent_name="coder")

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "swarm_agent"
        assert isinstance(tool.executor, SwarmAgentExecutor)
        tool.executor.close()

    def test_created_tool_is_callable_end_to_end(self, tmp_path):
        db_config = _make_db_config(tmp_path)
        tool = SwarmAgentTool.create(conv_state=None, db_config=db_config, agent_name="coder")[0]

        obs = tool(SwarmAgentAction(command="create_artifact", name="readme.md", path="shared/readme.md"))

        assert obs.rowcount == 1
        tool.executor.close()

        db = SwarmDB.from_config(db_config)
        assert db.get_artifacts()[0]["name"] == "readme.md"
        db.close()
