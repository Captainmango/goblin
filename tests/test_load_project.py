"""Tests for V1Blueprint._load_project — filesystem-based project loading."""

from __future__ import annotations

import json

import pytest

from goblin.blueprint import V1Blueprint
from goblin.hooks import HOOK_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint() -> V1Blueprint:
    return V1Blueprint({"name": "test_bp", "phases": [{"id": "s1", "prompt": "hi"}]})


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(HOOK_REGISTRY)
    yield
    HOOK_REGISTRY.clear()
    HOOK_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestLoadProjectSettings:
    def test_stores_settings_path(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        settings_file = settings_dir / "settings.json"
        settings_file.write_text(json.dumps({}))

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert bp.project_settings_path == str(settings_file)

    def test_no_settings_file_leaves_path_none(self, tmp_path):
        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert bp.project_settings_path is None

    def test_stores_project_path(self, tmp_path):
        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert bp.project_path == str(tmp_path)


# ---------------------------------------------------------------------------
# References (CLAUDE.md)
# ---------------------------------------------------------------------------


class TestLoadProjectRefs:
    def test_loads_root_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Root instructions")

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "Root instructions" in bp.project_refs

    def test_loads_dotclaude_claude_md(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("Dot-claude instructions")

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "Dot-claude instructions" in bp.project_refs

    def test_concatenates_both_refs(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Root ref")
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("Nested ref")

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "Root ref" in bp.project_refs
        assert "Nested ref" in bp.project_refs

    def test_no_refs_produces_empty_string(self, tmp_path):
        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert bp.project_refs == ""


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------


class TestLoadProjectAgents:
    def test_loads_agent_md_files(self, tmp_path):
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text("You are a code reviewer")

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "reviewer" in bp.project_agents
        assert bp.project_agents["reviewer"].prompt == "You are a code reviewer"
        assert bp.project_agents["reviewer"].description == "reviewer"

    def test_loads_multiple_agents(self, tmp_path):
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text("reviewer prompt")
        (agents_dir / "planner.md").write_text("planner prompt")

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert len(bp.project_agents) == 2
        assert "reviewer" in bp.project_agents
        assert "planner" in bp.project_agents

    def test_ignores_non_md_files(self, tmp_path):
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text("reviewer prompt")
        (agents_dir / "notes.txt").write_text("not an agent")

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "reviewer" in bp.project_agents
        assert "notes" not in bp.project_agents

    def test_no_agents_dir_leaves_empty(self, tmp_path):
        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert bp.project_agents == {}


# ---------------------------------------------------------------------------
# Shell command hooks from settings
# ---------------------------------------------------------------------------


class TestLoadProjectHooksFromSettings:
    def test_registers_post_tool_hooks(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        settings = {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "echo post"}]}]}}
        (settings_dir / "settings.json").write_text(json.dumps(settings))

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "project_post_tool" in HOOK_REGISTRY

    def test_registers_pre_tool_hooks(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo pre"}]}]}}
        (settings_dir / "settings.json").write_text(json.dumps(settings))

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "project_pre_tool" in HOOK_REGISTRY

    def test_skips_non_command_hooks(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        settings = {"hooks": {"PostToolUse": [{"hooks": [{"type": "other", "command": "echo nope"}]}]}}
        (settings_dir / "settings.json").write_text(json.dumps(settings))

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "project_post_tool" not in HOOK_REGISTRY

    def test_no_hooks_in_settings(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(json.dumps({}))

        bp = _make_blueprint()
        bp._load_project(str(tmp_path))

        assert "project_pre_tool" not in HOOK_REGISTRY
        assert "project_post_tool" not in HOOK_REGISTRY
