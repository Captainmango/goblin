"""Tests for V1Blueprint._parse_step — step type dispatch."""

from __future__ import annotations

import pytest

from goblin.blueprint import V1Blueprint
from goblin.step import PromptStep, LoadProjectStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(steps: list[dict]) -> V1Blueprint:
    return V1Blueprint({"name": "test_wf", "steps": steps})


# ---------------------------------------------------------------------------
# _parse_step
# ---------------------------------------------------------------------------


class TestParseStepPrompt:
    def test_prompt_step_creates_prompt_step(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do something"}])
        assert isinstance(bp.steps[0], PromptStep)

    def test_prompt_step_stores_id(self):
        bp = _make_blueprint([{"id": "my_step", "prompt": "hello"}])
        assert bp.steps[0].id == "my_step"

    def test_prompt_step_stores_template(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "fix {repo}"}])
        assert bp.steps[0].prompt_template == "fix {repo}"

    def test_prompt_step_stores_allowed_tools(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "hi", "allowed_tools": ["Bash", "Read"]}]
        )
        assert bp.steps[0].allowed_tools == ["Bash", "Read"]

    def test_prompt_step_allowed_tools_default_empty(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        assert bp.steps[0].allowed_tools == []

    def test_prompt_step_stores_condition(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "hi", "condition": "{{ steps.a.success }}"}]
        )
        assert bp.steps[0].condition == "{{ steps.a.success }}"

    def test_prompt_step_condition_default_none(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        assert bp.steps[0].condition is None

    def test_prompt_step_stores_foreach(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi", "foreach": "3"}])
        assert bp.steps[0].foreach == "3"

    def test_prompt_step_stores_on_failure(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "hi", "on_failure": {"action": "skip"}}]
        )
        assert bp.steps[0].on_failure == {"action": "skip"}

    def test_prompt_step_stores_hooks_config(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "hi", "hooks": {"post_tool": ["run_tests"]}}]
        )
        assert bp.steps[0].hooks_config == {"post_tool": ["run_tests"]}


class TestParseStepLoadProject:
    def test_load_project_step_creates_load_project_step(self):
        bp = _make_blueprint([{"id": "s1", "load_project": "/path/to/project"}])
        assert isinstance(bp.steps[0], LoadProjectStep)

    def test_load_project_step_stores_id(self):
        bp = _make_blueprint([{"id": "load_it", "load_project": "/tmp/proj"}])
        assert bp.steps[0].id == "load_it"

    def test_load_project_step_stores_path_template(self):
        bp = _make_blueprint([{"id": "s1", "load_project": "{clone_dir}"}])
        assert bp.steps[0].path_template == "{clone_dir}"


class TestParseStepInvalid:
    def test_step_without_prompt_or_load_project_raises(self):
        with pytest.raises(ValueError, match="must have either"):
            _make_blueprint([{"id": "bad"}])


class TestParseMultipleSteps:
    def test_mixed_step_types(self):
        bp = _make_blueprint(
            [
                {"id": "load", "load_project": "/proj"},
                {"id": "run", "prompt": "do stuff"},
            ]
        )
        assert isinstance(bp.steps[0], LoadProjectStep)
        assert isinstance(bp.steps[1], PromptStep)
        assert len(bp.steps) == 2
