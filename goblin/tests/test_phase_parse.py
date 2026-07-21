"""Tests for V1Blueprint._parse_phase — phase type dispatch."""

from __future__ import annotations

import pytest

from goblin.blueprint import V1Blueprint
from goblin.phase import LoadProjectPhase, PromptPhase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(phases: list[dict]) -> V1Blueprint:
    return V1Blueprint({"name": "test_bp", "phases": phases})


# ---------------------------------------------------------------------------
# _parse_phase
# ---------------------------------------------------------------------------


class TestParsePhasePrompt:
    def test_prompt_phase_creates_prompt_phase(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do something"}])
        assert isinstance(bp.phases[0], PromptPhase)

    def test_prompt_phase_stores_id(self):
        bp = _make_blueprint([{"id": "my_phase", "prompt": "hello"}])
        assert bp.phases[0].id == "my_phase"

    def test_prompt_phase_stores_template(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "fix {repo}"}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.prompt_template == "fix {repo}"

    def test_prompt_phase_stores_allowed_tools(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "allowed_tools": ["Bash", "Read"]}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.allowed_tools == ["Bash", "Read"]

    def test_prompt_phase_allowed_tools_default_empty(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.allowed_tools == []

    def test_prompt_phase_stores_condition(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "condition": "{{ steps.a.success }}"}])
        assert bp.phases[0].condition == "{{ steps.a.success }}"

    def test_prompt_phase_condition_default_none(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        assert bp.phases[0].condition is None

    def test_prompt_phase_stores_foreach(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "foreach": "3"}])
        assert bp.phases[0].foreach == "3"

    def test_prompt_phase_stores_on_failure(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "on_failure": {"action": "skip"}}])
        assert bp.phases[0].on_failure == {"action": "skip"}

    def test_prompt_phase_stores_hooks_config(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "hooks": {"post_tool": ["run_tests"]}}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.hooks_config == {"post_tool": ["run_tests"]}


class TestParsePhaseMcpServers:
    def test_prompt_phase_stores_mcp_servers(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "mcp_servers": {"gh": {"command": "npx"}}}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.mcp_servers_config == {"gh": {"command": "npx"}}

    def test_prompt_phase_mcp_servers_default_none(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.mcp_servers_config is None


class TestParsePhaseLoadProject:
    def test_load_project_phase_creates_load_project_phase(self):
        bp = _make_blueprint([{"id": "s1", "load_project": "/path/to/project"}])
        assert isinstance(bp.phases[0], LoadProjectPhase)

    def test_load_project_phase_stores_id(self):
        bp = _make_blueprint([{"id": "load_it", "load_project": "/tmp/proj"}])
        assert bp.phases[0].id == "load_it"

    def test_load_project_phase_stores_path_template(self):
        bp = _make_blueprint([{"id": "s1", "load_project": "{clone_dir}"}])
        phase = bp.phases[0]
        assert isinstance(phase, LoadProjectPhase)
        assert phase.path_template == "{clone_dir}"


class TestParsePhaseInvalid:
    def test_phase_without_prompt_or_load_project_raises(self):
        with pytest.raises(ValueError, match="must have"):
            _make_blueprint([{"id": "bad"}])


class TestParseMultiplePhases:
    def test_mixed_phase_types(self):
        bp = _make_blueprint(
            [
                {"id": "load", "load_project": "/proj"},
                {"id": "run", "prompt": "do stuff"},
            ]
        )
        assert isinstance(bp.phases[0], LoadProjectPhase)
        assert isinstance(bp.phases[1], PromptPhase)
        assert len(bp.phases) == 2
