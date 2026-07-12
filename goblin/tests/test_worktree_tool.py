"""Tests for the typed Worktree tool.

These exercise the ToolDefinition/ToolExecutor pair directly against temporary
git repositories -- no OpenHands SDK conversation/agent machinery is started,
per .claude/rules/testing.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase
from goblin.tools.tool_map import resolve_tools
from goblin.tools.worktree_tool import (
    WorktreeAction,
    WorktreeExecutor,
    WorktreeObservation,
    WorktreeTool,
)


DEFAULT_BASE_DIR = ".goblin/worktrees"


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init", "-b", "main")
    _git(str(repo), "config", "user.email", "test@example.com")
    _git(str(repo), "config", "user.name", "Test")
    (repo / "file.txt").write_text("hello")
    _git(str(repo), "add", ".")
    _git(str(repo), "commit", "-m", "init")
    _git(str(repo), "branch", "develop")
    return repo


def _sha(repo, ref: str = "HEAD") -> str:
    return _git(str(repo), "rev-parse", "--verify", ref).stdout.strip()


class TestWorktreeList:
    def test_list_shows_main_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))
        obs = executor(WorktreeAction(command="list"))
        assert isinstance(obs, WorktreeObservation)
        assert obs.is_error is False
        assert len(obs.worktrees) == 1
        assert obs.worktrees[0]["is_main"] is True
        assert obs.worktrees[0]["path"] == str(repo.resolve())


class TestWorktreeCreate:
    def test_create_from_branch(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))

        assert obs.is_error is False
        wt_path = repo / DEFAULT_BASE_DIR / "wt"
        assert (wt_path / "file.txt").exists()

        obs = executor(WorktreeAction(command="list"))
        paths = {w["path"] for w in obs.worktrees}
        assert str(wt_path.resolve()) in paths

    def test_create_from_sha(self, tmp_path):
        repo = _make_repo(tmp_path)
        sha = _sha(repo)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref=sha))

        assert obs.is_error is False
        assert (repo / DEFAULT_BASE_DIR / "wt" / "file.txt").exists()

    def test_create_from_tag(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(str(repo), "tag", "v1")
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="v1"))

        assert obs.is_error is False
        assert (repo / DEFAULT_BASE_DIR / "wt" / "file.txt").exists()

    def test_create_new_branch(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="feature-wt", ref="feature", create_branch=True))

        assert obs.is_error is False
        wt_path = repo / DEFAULT_BASE_DIR / "feature-wt"
        assert (wt_path / "file.txt").exists()

        obs = executor(WorktreeAction(command="list"))
        feature_wt = next(
            (w for w in obs.worktrees if Path(w["path"]).resolve() == wt_path.resolve()),
            None,
        )
        assert feature_wt is not None
        assert "feature" in feature_wt.get("branch", "")


class TestWorktreeLinkDeps:
    def test_create_links_dependencies_explicitly(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "pkg").write_text("shared")
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop", deps=["node_modules"]))

        assert obs.is_error is False
        wt = repo / DEFAULT_BASE_DIR / "wt"
        assert (wt / "node_modules").is_symlink()
        assert (wt / "node_modules" / "pkg").read_text() == "shared"
        assert obs.linked == ["node_modules"]

    def test_create_auto_links_from_config(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / ".venv").mkdir()
        (repo / ".venv" / "bin").mkdir()
        executor = WorktreeExecutor(repo_root=str(repo), deps=[".venv"], auto_link=True)

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))

        assert obs.is_error is False
        wt = repo / DEFAULT_BASE_DIR / "wt"
        assert (wt / ".venv").is_symlink()
        assert (wt / ".venv" / "bin").is_dir()

    def test_create_explicit_auto_link_false_overrides_config(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "node_modules").mkdir()
        executor = WorktreeExecutor(repo_root=str(repo), deps=["node_modules"], auto_link=True)

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop", auto_link=False))

        assert obs.is_error is False
        wt = repo / DEFAULT_BASE_DIR / "wt"
        assert not (wt / "node_modules").exists()

    def test_create_only_links_existing_dependencies(self, tmp_path):
        repo = _make_repo(tmp_path)
        # node_modules does not exist in the main worktree
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop", deps=["node_modules"]))

        assert obs.is_error is False
        wt = repo / DEFAULT_BASE_DIR / "wt"
        assert not (wt / "node_modules").exists()
        assert obs.linked in (None, [])

    def test_link_deps_command(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "pkg").write_text("shared")
        executor = WorktreeExecutor(repo_root=str(repo), deps=["node_modules"])

        # Create without linking, then link afterwards
        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is False

        obs = executor(WorktreeAction(command="link_deps", path="wt"))
        assert obs.is_error is False
        wt = repo / DEFAULT_BASE_DIR / "wt"
        assert (wt / "node_modules").is_symlink()
        assert obs.linked == ["node_modules"]

    def test_link_deps_skips_existing_target(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "node_modules").mkdir()
        executor = WorktreeExecutor(repo_root=str(repo), deps=["node_modules"])

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is False

        wt = repo / DEFAULT_BASE_DIR / "wt"
        (wt / "node_modules").mkdir()

        obs = executor(WorktreeAction(command="link_deps", path="wt"))
        assert obs.is_error is False
        assert not (wt / "node_modules").is_symlink()
        assert "Skipped" in obs.text


class TestWorktreeRemove:
    def test_remove_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))
        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is False

        obs = executor(WorktreeAction(command="remove", path="wt"))
        assert obs.is_error is False
        assert "Removed worktree" in obs.text
        assert not (repo / DEFAULT_BASE_DIR / "wt").exists()

    def test_remove_force_dirty_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))
        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is False

        wt = repo / DEFAULT_BASE_DIR / "wt"
        (wt / "dirty.txt").write_text("x")

        obs = executor(WorktreeAction(command="remove", path="wt"))
        assert obs.is_error is True

        obs = executor(WorktreeAction(command="remove", path="wt", force=True))
        assert obs.is_error is False
        assert not wt.exists()

    def test_remove_main_worktree_rejected(self, tmp_path):
        repo = _make_repo(tmp_path)
        # Use base_dir="." so the main worktree path validates as inside base_dir
        executor = WorktreeExecutor(repo_root=str(repo), base_dir=".")

        obs = executor(WorktreeAction(command="remove", path="."))

        assert obs.is_error is True
        assert "main worktree" in obs.text.lower()

    def test_remove_absolute_path_rejected(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="remove", path=str(repo)))

        assert obs.is_error is True
        assert "absolute" in obs.text.lower()

    def test_remove_outside_managed_dir_rejected(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="remove", path="../outside"))

        assert obs.is_error is True


class TestWorktreeSafety:
    def test_create_path_traversal_rejected(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="../outside", ref="develop"))

        assert obs.is_error is True

    def test_create_overlap_existing_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is False

        obs = executor(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is True
        assert "overlaps" in obs.text.lower()

    def test_create_invalid_ref(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="does-not-exist"))

        assert obs.is_error is True
        assert "invalid ref" in obs.text.lower()

    def test_create_invalid_branch_name(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="create", path="wt", ref="bad/branch?", create_branch=True))

        assert obs.is_error is True
        assert "invalid branch" in obs.text.lower()

    def test_non_git_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        executor = WorktreeExecutor(repo_root=str(empty))

        obs = executor(WorktreeAction(command="list"))

        assert obs.is_error is True
        assert "not inside a git repository" in obs.text.lower()

    def test_gitignore_warning(self, tmp_path):
        repo = _make_repo(tmp_path)
        executor = WorktreeExecutor(repo_root=str(repo))

        obs = executor(WorktreeAction(command="list"))

        assert obs.is_error is False
        assert obs.warnings is not None
        assert any("not ignored" in w.lower() for w in obs.warnings)


class TestWorktreeTool:
    def test_resolve_tools_includes_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        params = {
            "repo_root": str(repo),
            "base_dir": DEFAULT_BASE_DIR,
            "deps": ["node_modules"],
            "auto_link": True,
        }
        tools = resolve_tools(["Worktree"], tool_params={"Worktree": params})

        assert len(tools) == 1
        assert tools[0].name == WorktreeTool.name
        assert tools[0].params == params

    def test_tool_create_returns_callable_tool(self, tmp_path):
        repo = _make_repo(tmp_path)
        tools = WorktreeTool.create(repo_root=str(repo))

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "worktree"
        assert isinstance(tool.executor, WorktreeExecutor)

        obs = tool(WorktreeAction(command="create", path="wt", ref="develop"))
        assert obs.is_error is False
        assert (repo / DEFAULT_BASE_DIR / "wt" / "file.txt").exists()


class TestWorktreeConfigRequirement:
    @pytest.mark.asyncio
    async def test_prompt_phase_requires_worktree_config(self, tmp_path):
        spec = {
            "version": "v1",
            "name": "test",
            "phases": [
                {
                    "id": "p1",
                    "prompt": "use worktree",
                    "allowed_tools": ["Worktree"],
                }
            ],
        }
        blueprint = V1Blueprint(spec)
        phase = blueprint.phases[0]
        assert isinstance(phase, PromptPhase)

        result = await phase.run({})

        assert result["success"] is False
        assert any("worktree configuration" in m.lower() for m in result["message"])
