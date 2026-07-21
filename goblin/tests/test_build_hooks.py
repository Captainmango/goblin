"""Tests for V1Blueprint._build_hooks — hook resolution from defaults and phase overrides."""

from __future__ import annotations

from typing import Any

import pytest
from goblin.blueprint import V1Blueprint
from goblin.hooks import HOOK_REGISTRY
from goblin.phase import PromptPhase
from openhands.sdk.hooks import HookConfig, HookDefinition

_hook_a = HookDefinition(command="echo hook_a")
_hook_b = HookDefinition(command="echo hook_b")


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


class TestBuildHooksDefaults:
    def test_uses_default_hooks_when_step_has_none(self):
        HOOK_REGISTRY["run_tests"] = _hook_a
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"post_tool": ["run_tests"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        assert len(result.post_tool_use) == 1
        assert result.post_tool_use[0].hooks[0] is _hook_a

    def test_pre_tool_defaults_used(self):
        HOOK_REGISTRY["check_prom"] = _hook_a
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"pre_tool": ["check_prom"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        assert len(result.pre_tool_use) == 1
        assert result.pre_tool_use[0].hooks[0] is _hook_a


class TestBuildHooksStepOverride:
    def test_step_hooks_override_defaults(self):
        HOOK_REGISTRY["default_hook"] = _hook_a
        HOOK_REGISTRY["step_hook"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["step_hook"]}}],
            defaults={"hooks": {"post_tool": ["default_hook"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        assert len(result.post_tool_use) == 1
        assert result.post_tool_use[0].hooks[0] is _hook_b

    def test_step_pre_tool_overrides_default_pre_tool(self):
        HOOK_REGISTRY["default_pre"] = _hook_a
        HOOK_REGISTRY["step_pre"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"pre_tool": ["step_pre"]}}],
            defaults={"hooks": {"pre_tool": ["default_pre"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        assert len(result.pre_tool_use) == 1
        assert result.pre_tool_use[0].hooks[0] is _hook_b


class TestBuildHooksNoHooks:
    def test_no_defaults_no_step_hooks(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do stuff"}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert result is None

    def test_unregistered_hook_name_skipped(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"post_tool": ["nonexistent_hook"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert result is None

    def test_callable_in_registry_skipped_by_build_hooks(self):
        """Callables in HOOK_REGISTRY are not picked up as tool-use hooks."""
        HOOK_REGISTRY["callable_hook"] = lambda tool_use: None
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"hooks": {"post_tool": ["callable_hook"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert result is None


class TestBuildHooksMerge:
    def test_blueprint_merge_flag_appends_step_hooks_to_defaults(self):
        HOOK_REGISTRY["default_post"] = _hook_a
        HOOK_REGISTRY["step_post"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["step_post"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        post_hooks = result.post_tool_use[0].hooks
        assert len(post_hooks) == 2
        assert _hook_a in post_hooks
        assert _hook_b in post_hooks

    def test_blueprint_merge_flag_defaults_run_first(self):
        HOOK_REGISTRY["default_post"] = _hook_a
        HOOK_REGISTRY["step_post"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["step_post"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        post_hooks = result.post_tool_use[0].hooks
        assert post_hooks[0] is _hook_a
        assert post_hooks[1] is _hook_b

    def test_step_merge_flag_overrides_blueprint_default(self):
        HOOK_REGISTRY["default_post"] = _hook_a
        HOOK_REGISTRY["step_post"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"merge": True, "post_tool": ["step_post"]}}],
            defaults={"hooks": {"post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        post_hooks = result.post_tool_use[0].hooks
        assert len(post_hooks) == 2
        assert _hook_a in post_hooks
        assert _hook_b in post_hooks

    def test_step_merge_false_reverts_to_override_when_blueprint_merges(self):
        HOOK_REGISTRY["default_post"] = _hook_a
        HOOK_REGISTRY["step_post"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"merge": False, "post_tool": ["step_post"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        post_hooks = result.post_tool_use[0].hooks
        assert len(post_hooks) == 1
        assert _hook_b in post_hooks
        assert _hook_a not in post_hooks

    def test_merge_mode_pre_and_post_both_concatenated(self):
        HOOK_REGISTRY["default_pre"] = _hook_a
        HOOK_REGISTRY["step_pre"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"pre_tool": ["step_pre"]}}],
            defaults={"hooks": {"merge": True, "pre_tool": ["default_pre"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        pre_hooks = result.pre_tool_use[0].hooks
        assert len(pre_hooks) == 2
        assert _hook_a in pre_hooks
        assert _hook_b in pre_hooks

    def test_override_step_overrides_pre_but_inherits_default_post(self):
        HOOK_REGISTRY["default_pre"] = _hook_a
        HOOK_REGISTRY["default_post"] = _hook_a
        HOOK_REGISTRY["step_pre"] = _hook_b
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"pre_tool": ["step_pre"]}}],
            defaults={"hooks": {"pre_tool": ["default_pre"], "post_tool": ["default_post"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        pre_hooks = result.pre_tool_use[0].hooks
        post_hooks = result.post_tool_use[0].hooks
        assert len(pre_hooks) == 1
        assert _hook_b in pre_hooks
        assert len(post_hooks) == 1
        assert _hook_a in post_hooks

    def test_unknown_hooks_skipped_in_merge_mode(self):
        HOOK_REGISTRY["known_hook"] = _hook_a
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "hooks": {"post_tool": ["unknown_hook"]}}],
            defaults={"hooks": {"merge": True, "post_tool": ["known_hook"]}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_hooks(phase)

        assert isinstance(result, HookConfig)
        post_hooks = result.post_tool_use[0].hooks
        assert len(post_hooks) == 1
        assert _hook_a in post_hooks
