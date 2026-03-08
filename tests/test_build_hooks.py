"""Tests for V1Blueprint._build_hooks — hook resolution from defaults and phase overrides."""

from __future__ import annotations

from typing import Any

import pytest

from goblin.blueprint import V1Blueprint
from goblin.hooks import HOOK_REGISTRY
from goblin.phase import PromptPhase


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


def _make_blueprint(phases: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
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
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

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
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

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
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

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
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        pre_matchers = hooks["PreToolUse"]
        assert len(pre_matchers) == 1


class TestBuildHooksNoHooks:
    def test_no_defaults_no_step_hooks(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do stuff"}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks

    def test_unregistered_hook_name_skipped(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"post_tool": ["nonexistent_hook"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        # Should not raise — unregistered names are silently skipped
        hooks = bp._build_hooks(phase)
        assert "PostToolUse" in hooks


class TestBuildHooksMerge:
    def test_blueprint_merge_flag_appends_step_hooks_to_defaults(self):
        HOOK_REGISTRY["default_post"] = _dummy_hook
        HOOK_REGISTRY["step_post"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["step_post"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        post_hooks = hooks["PostToolUse"][0].hooks
        assert len(post_hooks) == 2
        assert _dummy_hook in post_hooks
        assert _another_hook in post_hooks

    def test_blueprint_merge_flag_defaults_run_first(self):
        HOOK_REGISTRY["default_post"] = _dummy_hook
        HOOK_REGISTRY["step_post"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["step_post"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        post_hooks = hooks["PostToolUse"][0].hooks
        assert post_hooks[0] is _dummy_hook
        assert post_hooks[1] is _another_hook

    def test_step_merge_flag_overrides_blueprint_default(self):
        HOOK_REGISTRY["default_post"] = _dummy_hook
        HOOK_REGISTRY["step_post"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"merge": True, "post_tool": ["step_post"]}}],
            defaults={"hooks": {"post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        post_hooks = hooks["PostToolUse"][0].hooks
        assert len(post_hooks) == 2
        assert _dummy_hook in post_hooks
        assert _another_hook in post_hooks

    def test_step_merge_false_reverts_to_override_when_blueprint_merges(self):
        HOOK_REGISTRY["default_post"] = _dummy_hook
        HOOK_REGISTRY["step_post"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"merge": False, "post_tool": ["step_post"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        post_hooks = hooks["PostToolUse"][0].hooks
        assert len(post_hooks) == 1
        assert _another_hook in post_hooks
        assert _dummy_hook not in post_hooks

    def test_merge_mode_pre_and_post_both_concatenated(self):
        HOOK_REGISTRY["default_pre"] = _dummy_hook
        HOOK_REGISTRY["step_pre"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"pre_tool": ["step_pre"]}}],
            defaults={"hooks": {"merge": True, "pre_tool": ["default_pre"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        pre_hooks = hooks["PreToolUse"][0].hooks
        assert len(pre_hooks) == 2
        assert _dummy_hook in pre_hooks
        assert _another_hook in pre_hooks

    def test_override_step_overrides_pre_but_inherits_default_post(self):
        HOOK_REGISTRY["default_pre"] = _dummy_hook
        HOOK_REGISTRY["default_post"] = _dummy_hook
        HOOK_REGISTRY["step_pre"] = _another_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"pre_tool": ["step_pre"]}}],
            defaults={"hooks": {"pre_tool": ["default_pre"], "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        pre_hooks = hooks["PreToolUse"][0].hooks
        post_hooks = hooks["PostToolUse"][0].hooks
        # pre_tool is overridden by step
        assert len(pre_hooks) == 1
        assert _another_hook in pre_hooks
        # post_tool falls back to defaults (not specified by step)
        assert len(post_hooks) == 1
        assert _dummy_hook in post_hooks

    def test_unknown_hooks_skipped_in_merge_mode(self):
        HOOK_REGISTRY["known_hook"] = _dummy_hook
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["unknown_hook"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["known_hook"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        hooks = bp._build_hooks(phase)

        post_hooks = hooks["PostToolUse"][0].hooks
        # unknown_hook silently dropped; known_hook from defaults present
        assert len(post_hooks) == 1
        assert _dummy_hook in post_hooks
