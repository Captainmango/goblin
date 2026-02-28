"""Tests for V1Blueprint._build_hooks — hook resolution from defaults and step overrides."""

from __future__ import annotations

import pytest

from goblin.blueprint import V1Blueprint
from goblin.hooks import HOOK_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy_hook(tool_use):
    pass


def _another_hook(tool_use):
    pass


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save and restore HOOK_REGISTRY around each test."""
    saved = dict(HOOK_REGISTRY)
    yield
    HOOK_REGISTRY.clear()
    HOOK_REGISTRY.update(saved)


def _make_blueprint(steps: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec = {"name": "test_wf", "steps": steps}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


# ---------------------------------------------------------------------------
# _build_hooks
# ---------------------------------------------------------------------------

class TestBuildHooksDefaults:
    def test_uses_default_hooks_when_step_has_none(self):
        HOOK_REGISTRY["run_tests"] = _dummy_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"post_tool": ["run_tests"]}},
        )
        step = bp.steps[0]
        hooks = bp._build_hooks(step)

        assert "PostToolUse" in hooks
        assert "PreToolUse" in hooks
        # The post_tool hook should contain our registered hook
        post_matchers = hooks["PostToolUse"]
        assert len(post_matchers) == 1

    def test_pre_tool_defaults_used(self):
        HOOK_REGISTRY["check_prom"] = _dummy_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"pre_tool": ["check_prom"]}},
        )
        step = bp.steps[0]
        hooks = bp._build_hooks(step)

        pre_matchers = hooks["PreToolUse"]
        assert len(pre_matchers) == 1


class TestBuildHooksStepOverride:
    def test_step_hooks_override_defaults(self):
        HOOK_REGISTRY["default_hook"] = _dummy_hook
        HOOK_REGISTRY["step_hook"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["step_hook"]}}],
            defaults={"hooks": {"post_tool": ["default_hook"]}},
        )
        step = bp.steps[0]
        hooks = bp._build_hooks(step)

        # Step-level overrides defaults entirely — default_hook should NOT be used
        post_matchers = hooks["PostToolUse"]
        assert len(post_matchers) == 1

    def test_step_pre_tool_overrides_default_pre_tool(self):
        HOOK_REGISTRY["default_pre"] = _dummy_hook
        HOOK_REGISTRY["step_pre"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"pre_tool": ["step_pre"]}}],
            defaults={"hooks": {"pre_tool": ["default_pre"]}},
        )
        step = bp.steps[0]
        hooks = bp._build_hooks(step)

        pre_matchers = hooks["PreToolUse"]
        assert len(pre_matchers) == 1


class TestBuildHooksNoHooks:
    def test_no_defaults_no_step_hooks(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do stuff"}])
        step = bp.steps[0]
        hooks = bp._build_hooks(step)

        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks

    def test_unregistered_hook_name_skipped(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"post_tool": ["nonexistent_hook"]}},
        )
        step = bp.steps[0]
        # Should not raise — unregistered names are silently skipped
        hooks = bp._build_hooks(step)
        assert "PostToolUse" in hooks
