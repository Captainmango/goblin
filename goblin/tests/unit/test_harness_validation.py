"""Tests for ACP harness semantic validation rules."""

from __future__ import annotations

from typing import Any

import yaml
from click.testing import CliRunner

from goblin.cli import cli
from goblin.validation import validate_blueprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_blueprint(tmp_path, data: dict[str, Any]) -> str:
    path = tmp_path / "bp.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


def _make_bp(
    phase: dict[str, Any],
    defaults: dict[str, Any] | None = None,
    workspace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bp: dict[str, Any] = {"name": "test", "phases": [phase]}
    if defaults is not None:
        bp["defaults"] = defaults
    if workspace is not None:
        bp["workspace"] = workspace
    return {"version": "v1", "blueprint": bp}


# ---------------------------------------------------------------------------
# Rule 1: ACP + allowed_tools
# ---------------------------------------------------------------------------


class TestACPAllowedTools:
    def test_acp_plus_allowed_tools_is_error(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp"},
                "allowed_tools": ["Bash", "Read"],
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(
            e["severity"] == "error" and "allowed_tools" in e["message"] and "acp" in e["message"].lower()
            for e in errors
        )

    def test_acp_in_defaults_plus_phase_allowed_tools_is_error(self, tmp_path):
        bp = _make_bp(
            {"id": "p1", "prompt": "hi", "allowed_tools": ["Bash"]},
            defaults={"harness": {"type": "acp"}},
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["severity"] == "error" and "allowed_tools" in e["message"] for e in errors)

    def test_openhands_plus_allowed_tools_passes(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "openhands"},
                "allowed_tools": ["Bash", "Read"],
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert not any("allowed_tools" in e.get("message", "") for e in errors)


# ---------------------------------------------------------------------------
# Rule 2: ACP + hooks
# ---------------------------------------------------------------------------


class TestACPHooks:
    def test_acp_plus_phase_hooks_is_error(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp"},
                "hooks": {"pre_tool": ["check_prometheus"]},
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(
            e["severity"] == "error" and e["path"] == "blueprint.phases[0].hooks" and "acp" in e["message"].lower()
            for e in errors
        )

    def test_acp_plus_defaults_hooks_is_error(self, tmp_path):
        bp = _make_bp(
            {"id": "p1", "prompt": "hi", "harness": {"type": "acp"}},
            defaults={"hooks": {"pre_tool": ["check_prometheus"]}},
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["severity"] == "error" and e["path"] == "blueprint.defaults.hooks" for e in errors)


# ---------------------------------------------------------------------------
# Rule 3: ACP + worktree
# ---------------------------------------------------------------------------


class TestACPWorktree:
    def test_acp_plus_phase_worktree_is_error(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp"},
                "worktree": {"base_dir": ".goblin/worktrees"},
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(
            e["severity"] == "error" and "worktree" in e["message"] and "acp" in e["message"].lower() for e in errors
        )

    def test_acp_plus_defaults_worktree_is_error(self, tmp_path):
        bp = _make_bp(
            {"id": "p1", "prompt": "hi", "harness": {"type": "acp"}},
            defaults={"worktree": {"base_dir": ".goblin/worktrees"}},
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["severity"] == "error" and "worktree" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# Rule 4: ACP + remote workspace
# ---------------------------------------------------------------------------


class TestACPRemoteWorkspace:
    def test_acp_plus_remote_workspace_is_error(self, tmp_path):
        bp = _make_bp(
            {"id": "p1", "prompt": "hi", "harness": {"type": "acp"}},
            workspace={"type": "remote", "host": "http://example.com"},
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(
            e["severity"] == "error" and "workspace.type" in e["path"] and "remote" in e["message"] for e in errors
        )


# ---------------------------------------------------------------------------
# Rule 6: custom server without command
# ---------------------------------------------------------------------------


class TestACPCustomServer:
    def test_custom_server_without_command_is_error(self, tmp_path):
        bp = _make_bp({"id": "p1", "prompt": "hi", "harness": {"type": "acp", "server": "custom"}})
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["severity"] == "error" and "custom" in e["message"] and "command" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# Rule 7: command with non-custom server is a warning
# ---------------------------------------------------------------------------


class TestACPCommandOverride:
    def test_command_with_non_custom_server_is_warning(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp", "server": "claude-code", "command": ["/bin/claude"]},
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["severity"] == "warning" and e["path"] == "blueprint.phases[0].harness.command" for e in errors)


# ---------------------------------------------------------------------------
# Step 4 compatibility: ACP + MCP servers
# ---------------------------------------------------------------------------


class TestACPMcpServers:
    def test_acp_plus_mcp_servers_passes_validation(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp"},
                "mcp_servers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    }
                },
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert not any("mcp" in e.get("message", "").lower() for e in errors)


# ---------------------------------------------------------------------------
# Rule 8: ACP + max_iterations is a warning
# ---------------------------------------------------------------------------


class TestACPMaxIterations:
    def test_acp_plus_max_iterations_is_warning(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp"},
                "max_iterations": 10,
            }
        )
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(
            e["severity"] == "warning" and "max_iterations" in e["message"] and "acp" in e["message"].lower()
            for e in errors
        )


# ---------------------------------------------------------------------------
# CLI behaviour: warnings do not fail validation
# ---------------------------------------------------------------------------


class TestValidateCLI:
    def test_warnings_only_exit_zero(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp", "server": "claude-code", "command": ["/bin/claude"]},
            }
        )
        path = _write_blueprint(tmp_path, bp)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", path])
        assert result.exit_code == 0
        assert "valid (with warnings)" in result.output

    def test_errors_exit_non_zero(self, tmp_path):
        bp = _make_bp(
            {
                "id": "p1",
                "prompt": "hi",
                "harness": {"type": "acp"},
                "allowed_tools": ["Bash"],
            }
        )
        path = _write_blueprint(tmp_path, bp)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", path])
        assert result.exit_code == 1
        assert "invalid" in result.output
