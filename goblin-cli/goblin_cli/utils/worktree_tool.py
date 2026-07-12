"""Typed git worktree tool for isolated working copies.

Agents can list, create, remove, prune, and link shared dependencies into git
worktrees through a safe, typed interface. All paths are sandboxed under a
managed base directory (``.goblin/worktrees/`` by default) and git refs are
validated before any destructive command runs.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
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

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

logger = logging.getLogger(__name__)


class WorktreeAction(Action):
    """Schema for worktree operations."""

    command: Literal["list", "create", "remove", "prune", "link_deps"] = Field(
        description="The worktree operation to perform."
    )
    path: str | None = Field(default=None, description="Relative path under the managed worktree base directory.")
    ref: str | None = Field(
        default=None,
        description="Required for `create`: branch name, tag, or commit SHA to check out.",
    )
    create_branch: bool = Field(
        default=False,
        description="For `create`: treat `ref` as a new branch name and create it with `git worktree add -b`.",
    )
    deps: list[str] | None = Field(
        default=None,
        description="Dependency directory names to symlink from the main worktree. Uses the configured list when omitted.",
    )
    auto_link: bool | None = Field(
        default=None,
        description="For `create`: explicitly enable/disable automatic dependency linking. Uses the configured value when omitted.",
    )
    force: bool = Field(
        default=False,
        description="For `remove`: pass `--force` to remove dirty or locked worktrees.",
    )


class WorktreeObservation(Observation):
    """Result of a worktree operation."""

    worktrees: list[dict] | None = Field(default=None, description="Worktrees returned by `list`.")
    worktree: dict | None = Field(default=None, description="The worktree created by `create`.")
    ref: str | None = Field(default=None, description="The resolved ref for `create`.")
    removed: list[str] | None = Field(default=None, description="Paths removed by `remove`.")
    pruned: bool | None = Field(default=None, description="True when `prune` succeeded.")
    linked: list[str] | None = Field(
        default=None, description="Dependency directories linked by `create` or `link_deps`."
    )
    warnings: list[str] | None = Field(
        default=None, description="Non-fatal warnings, e.g. when the worktree directory is not gitignored."
    )


TOOL_DESCRIPTION = """Manage git worktrees for isolated working copies.
* `list`: show all registered worktrees with their path, HEAD commit, branch, and whether each is the main worktree.
* `create`: add a new worktree for a ref under the managed worktree directory. `ref` can be a branch, tag, or commit SHA. Set `create_branch: true` to create a new branch. Dependency directories can be symlinked automatically when configured.
* `remove`: delete a worktree directory and detach it. Cannot delete the main worktree or paths outside the managed directory.
* `prune`: clean up stale worktree administrative entries.
* `link_deps`: symlink shared dependency directories (e.g. `node_modules`, `.venv`) from the main worktree into an existing worktree.
"""  # noqa: E501


class WorktreeExecutor(ToolExecutor[WorktreeAction, WorktreeObservation]):
    """Runs git worktree commands inside a sandboxed managed directory.

    `repo_root`, `base_dir`, `deps`, and `auto_link` are injected by
    `PromptPhase` from blueprint configuration. The executor is stateless enough
    to survive OpenHands' conversation state snapshot.
    """

    def __init__(
        self,
        repo_root: str,
        base_dir: str = ".goblin/worktrees",
        deps: list[str] | None = None,
        auto_link: bool = False,
    ):
        self._repo_root = Path(repo_root).resolve()
        self._base_dir_name = base_dir
        self._deps = list(deps or [])
        self._auto_link = auto_link
        self._gitignore_warned = False

    @property
    def _base_dir(self) -> Path:
        return self._repo_root / self._base_dir_name

    def close(self) -> None:
        """No persistent resources to release."""
        return None

    def __call__(
        self,
        action: WorktreeAction,
        conversation: "LocalConversation | None" = None,
    ) -> WorktreeObservation:
        err = self._validate_in_git()
        if err is not None:
            return err

        warnings: list[str] = []
        gitignore_warning = self._check_gitignore()
        if gitignore_warning is not None:
            warnings.append(gitignore_warning)

        try:
            if action.command == "list":
                return self._handle_list(warnings)
            if action.command == "create":
                return self._handle_create(action, warnings)
            if action.command == "remove":
                return self._handle_remove(action, warnings)
            if action.command == "prune":
                return self._handle_prune(warnings)
            if action.command == "link_deps":
                return self._handle_link_deps(action, warnings)
        except Exception as exc:
            return self._error(str(exc))

        return self._error(f"Unknown command: {action.command}")

    # --------------------------------------------------------------------- #
    # Git helpers
    # --------------------------------------------------------------------- #
    def _git(
        self,
        args: list[str],
        cwd: str | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or str(self._repo_root),
            capture_output=True,
            text=True,
            check=check,
        )

    def _error(self, message: str) -> WorktreeObservation:
        return WorktreeObservation.from_text(text=f"Error: {message}", is_error=True)

    def _git_dir(self) -> Path | None:
        res = self._git(["rev-parse", "--show-toplevel"])
        if res.returncode != 0:
            return None
        return Path(res.stdout.strip()).resolve()

    def _main_worktree(self) -> Path | None:
        res = self._git(["worktree", "list", "--porcelain"])
        if res.returncode != 0:
            return None
        for block in res.stdout.strip().split("\n\n"):
            for line in block.splitlines():
                if line.startswith("worktree "):
                    return Path(line[len("worktree ") :]).resolve()
        return None

    def _all_worktrees(self) -> list[dict]:
        res = self._git(["worktree", "list", "--porcelain"])
        if res.returncode != 0:
            return []
        worktrees: list[dict] = []
        main_seen = False
        for block in res.stdout.strip().split("\n\n"):
            wt: dict = {}
            for line in block.splitlines():
                if line.startswith("worktree "):
                    wt["path"] = line[len("worktree ") :]
                elif line.startswith("HEAD "):
                    wt["head"] = line[len("HEAD ") :]
                elif line.startswith("branch "):
                    wt["branch"] = line[len("branch ") :]
                elif line == "bare":
                    wt["bare"] = True
            if "path" in wt:
                wt["is_main"] = not main_seen
                main_seen = True
                worktrees.append(wt)
        return worktrees

    # --------------------------------------------------------------------- #
    # Validation
    # --------------------------------------------------------------------- #
    def _validate_in_git(self) -> WorktreeObservation | None:
        if self._git_dir() is None:
            return self._error("Not inside a git repository")
        return None

    def _validate_path(self, path: str | None, must_exist: bool = False) -> Path | WorktreeObservation:
        if not path:
            return self._error("`path` is required")
        p = Path(path)
        if p.is_absolute():
            return self._error("Absolute paths are not allowed")

        base_resolved = self._base_dir.resolve()
        full = (self._base_dir / path).resolve()
        try:
            full.relative_to(base_resolved)
        except ValueError:
            return self._error("Path escapes the managed worktree directory")

        if must_exist and not full.exists():
            return self._error(f"Path does not exist: {path}")

        return full

    def _validate_ref(self, ref: str | None, create_branch: bool) -> str | WorktreeObservation:
        if not ref:
            return self._error("`ref` is required for create")

        if create_branch:
            fmt = self._git(["check-ref-format", "--branch", ref])
            if fmt.returncode != 0:
                return self._error(f"Invalid branch name: {ref}")
            return ref

        res = self._git(["rev-parse", "--verify", ref])
        if res.returncode != 0:
            return self._error(f"Invalid ref: {ref}")
        return res.stdout.strip()

    def _check_gitignore(self) -> str | None:
        if self._gitignore_warned:
            return None
        if self._git_dir() is None:
            return None

        # Check the base directory if it exists, otherwise its parent ``.goblin/``.
        check_path = self._base_dir if self._base_dir.exists() else self._repo_root / ".goblin"
        res = self._git(["check-ignore", "-q", str(check_path)])
        self._gitignore_warned = True
        if res.returncode == 0:
            return None
        if res.returncode == 1:
            rel = check_path.relative_to(self._repo_root)
            return f"Warning: {rel} is not ignored by git. Add it to .gitignore to avoid polluting the main checkout."
        return None

    def _link_deps(self, target: Path, deps: list[str]) -> tuple[list[str], list[str]]:
        main = self._main_worktree()
        if main is None:
            return [], ["Could not resolve main worktree for dependency linking"]

        main_resolved = main.resolve()
        target_resolved = target.resolve()
        linked: list[str] = []
        skipped: list[str] = []

        for dep in deps:
            if not dep:
                skipped.append(dep)
                continue

            src = (main_resolved / dep).resolve()
            try:
                src.relative_to(main_resolved)
            except ValueError:
                skipped.append(dep)
                continue
            if not src.exists():
                continue

            dst = (target_resolved / dep).resolve()
            try:
                dst.relative_to(target_resolved)
            except ValueError:
                skipped.append(dep)
                continue
            if dst.exists() or dst.is_symlink():
                skipped.append(dep)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(src, dst, target_is_directory=True)
            linked.append(dep)

        return linked, skipped

    def _overlap_check(self, target: Path) -> WorktreeObservation | None:
        target_resolved = target.resolve()

        main = self._main_worktree()
        if main is not None:
            main_resolved = main.resolve()
            if target_resolved == main_resolved:
                return self._error("Worktree path overlaps the main worktree")
            # A path that contains the main worktree would overlap it.
            if main_resolved.is_relative_to(target_resolved):
                return self._error("Worktree path overlaps the main worktree")

        for wt in self._all_worktrees():
            if wt.get("is_main"):
                continue
            try:
                wt_path = Path(wt["path"]).resolve()
            except Exception:
                continue
            if target_resolved == wt_path:
                return self._error("Worktree path overlaps an existing worktree")
            if target_resolved.is_relative_to(wt_path):
                return self._error("Worktree path overlaps an existing worktree")
            if wt_path.is_relative_to(target_resolved):
                return self._error("Worktree path overlaps an existing worktree")

        return None

    # --------------------------------------------------------------------- #
    # Command handlers
    # --------------------------------------------------------------------- #
    def _handle_list(self, warnings: list[str]) -> WorktreeObservation:
        worktrees = self._all_worktrees()
        lines = [
            f"{'*' if wt.get('is_main') else ' '} {wt.get('path')} [{wt.get('branch') or wt.get('head', '?')}]"
            for wt in worktrees
        ]
        text = "\n".join(lines) if lines else "No worktrees"
        return WorktreeObservation.from_text(
            text=text,
            worktrees=worktrees,
            warnings=warnings or None,
        )

    def _handle_create(self, action: WorktreeAction, warnings: list[str]) -> WorktreeObservation:
        path_result = self._validate_path(action.path)
        if isinstance(path_result, WorktreeObservation):
            return path_result
        target = path_result

        self._base_dir.mkdir(parents=True, exist_ok=True)

        overlap = self._overlap_check(target)
        if overlap is not None:
            return overlap

        ref_result = self._validate_ref(action.ref, action.create_branch)
        if isinstance(ref_result, WorktreeObservation):
            return ref_result
        ref: str = ref_result

        if action.create_branch:
            res = self._git(["worktree", "add", "-b", ref, str(target)])
        else:
            res = self._git(["worktree", "add", str(target), ref])
        if res.returncode != 0:
            return self._error(res.stderr.strip() or "git worktree add failed")

        linked: list[str] = []
        deps_to_link: list[str] = []
        if action.deps is not None:
            deps_to_link = list(action.deps)

        should_link = bool(deps_to_link) or action.auto_link is True or (action.auto_link is None and self._auto_link)
        if should_link:
            if not deps_to_link:
                deps_to_link = list(self._deps)
            linked, _skipped = self._link_deps(target, deps_to_link)

        worktree = next(
            (wt for wt in self._all_worktrees() if Path(wt["path"]).resolve() == target.resolve()),
            None,
        )

        rel_target = target.relative_to(self._repo_root)
        text = f"Created worktree at {rel_target}"
        if linked:
            text += f"\nLinked dependencies: {', '.join(linked)}"

        return WorktreeObservation.from_text(
            text=text,
            worktree=worktree,
            ref=ref_result if not action.create_branch else action.ref,
            linked=linked or None,
            warnings=warnings or None,
        )

    def _handle_remove(self, action: WorktreeAction, warnings: list[str]) -> WorktreeObservation:
        path_result = self._validate_path(action.path)
        if isinstance(path_result, WorktreeObservation):
            return path_result
        target = path_result

        main = self._main_worktree()
        if main is not None and target.resolve() == main.resolve():
            return self._error("Cannot remove the main worktree")

        args = ["worktree", "remove"]
        if action.force:
            args.append("--force")
        args.append(str(target))

        res = self._git(args)
        if res.returncode != 0:
            return self._error(res.stderr.strip() or "git worktree remove failed")

        rel_target = target.relative_to(self._repo_root)
        return WorktreeObservation.from_text(
            text=f"Removed worktree at {rel_target}",
            removed=[str(target)],
            warnings=warnings or None,
        )

    def _handle_prune(self, warnings: list[str]) -> WorktreeObservation:
        res = self._git(["worktree", "prune"])
        if res.returncode != 0:
            return self._error(res.stderr.strip() or "git worktree prune failed")
        return WorktreeObservation.from_text(
            text="Pruned worktrees",
            pruned=True,
            warnings=warnings or None,
        )

    def _handle_link_deps(self, action: WorktreeAction, warnings: list[str]) -> WorktreeObservation:
        path_result = self._validate_path(action.path, must_exist=True)
        if isinstance(path_result, WorktreeObservation):
            return path_result
        target = path_result

        deps = list(action.deps) if action.deps is not None else list(self._deps)
        if not deps:
            return self._error("No dependencies configured or provided")

        linked, skipped = self._link_deps(target, deps)
        text = f"Linked dependencies: {', '.join(linked)}" if linked else "No dependencies linked"
        if skipped:
            text += f"\nSkipped: {', '.join(skipped)}"

        return WorktreeObservation.from_text(
            text=text,
            linked=linked or None,
            warnings=warnings or None,
        )


class WorktreeTool(ToolDefinition[WorktreeAction, WorktreeObservation]):
    """Typed git worktree tool for prompt agents."""

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Sequence["WorktreeTool"]:
        repo_root: str = kwargs.get("repo_root", os.getcwd())
        base_dir: str = kwargs.get("base_dir", ".goblin/worktrees")
        deps: list[str] = list(kwargs.get("deps", []))
        auto_link: bool = kwargs.get("auto_link", False)
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=WorktreeAction,
                observation_type=WorktreeObservation,
                annotations=ToolAnnotations(
                    title="worktree",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=WorktreeExecutor(
                    repo_root=repo_root,
                    base_dir=base_dir,
                    deps=deps,
                    auto_link=auto_link,
                ),
            )
        ]


register_tool(WorktreeTool.name, WorktreeTool)
