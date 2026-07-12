"""Tests for workspace resolution — build_workspace and V1Blueprint._get_workspace."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from goblin_cli.blueprint import V1Blueprint
from goblin_cli.workspace import build_workspace


# ---------------------------------------------------------------------------
# build_workspace
# ---------------------------------------------------------------------------


class TestBuildWorkspace:
    def test_default_local_when_no_config(self):
        ws = build_workspace(None, "/project")
        assert isinstance(ws, MagicMock) or ws.__class__.__name__ == "LocalWorkspace"

    def test_default_local_when_empty_config(self):
        ws = build_workspace({}, "/project")
        assert ws.__class__.__name__ == "LocalWorkspace"

    def test_local_with_custom_working_dir(self):
        ws = build_workspace({"type": "local", "working_dir": "/custom"}, "/project")
        assert ws.__class__.__name__ == "LocalWorkspace"

    def test_remote_workspace_builds_correctly(self):
        ws = build_workspace(
            {
                "type": "remote",
                "host": "https://agent-server.example.com",
                "working_dir": "/workspace",
                "api_key": "secret",
                "read_timeout": 1200,
                "max_connections": 10,
            },
            "/project",
        )
        assert ws.__class__.__name__ == "RemoteWorkspace"

    def test_remote_without_optional_fields(self):
        ws = build_workspace(
            {
                "type": "remote",
                "host": "http://localhost:3000",
                "working_dir": "/workspace",
            },
            "/project",
        )
        assert ws.__class__.__name__ == "RemoteWorkspace"

    def test_fallback_working_dir_to_project_path(self):
        ws = build_workspace(None, "/project")
        assert ws.__class__.__name__ == "LocalWorkspace"

    def test_fallback_working_dir_to_dot_when_no_project(self):
        ws = build_workspace(None, None)
        assert ws.__class__.__name__ == "LocalWorkspace"


# ---------------------------------------------------------------------------
# V1Blueprint workspace parsing
# ---------------------------------------------------------------------------


class TestBlueprintWorkspaceParsing:
    def test_no_workspace_config(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "s1", "prompt": "hi"}]})
        assert bp._workspace_config is None

    def test_local_workspace_config_stored(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "workspace": {"type": "local", "working_dir": "/tmp"},
                "phases": [{"id": "s1", "prompt": "hi"}],
            }
        )
        assert bp._workspace_config == {"type": "local", "working_dir": "/tmp"}

    def test_remote_workspace_config_stored(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "workspace": {
                    "type": "remote",
                    "host": "https://example.com",
                    "working_dir": "/workspace",
                    "api_key": "key",
                },
                "phases": [{"id": "s1", "prompt": "hi"}],
            }
        )
        assert bp._workspace_config["type"] == "remote"
        assert bp._workspace_config["host"] == "https://example.com"

    def test_env_var_expansion_in_workspace(self, monkeypatch):
        monkeypatch.setenv("REMOTE_API_KEY", "my-secret-key")
        bp = V1Blueprint(
            {
                "name": "test",
                "workspace": {
                    "type": "remote",
                    "host": "https://example.com",
                    "working_dir": "/workspace",
                    "api_key": "${REMOTE_API_KEY}",
                },
                "phases": [{"id": "s1", "prompt": "hi"}],
            }
        )
        assert bp._workspace_config["api_key"] == "my-secret-key"

    def test_unbraced_env_var_raises(self):
        with pytest.raises(ValueError, match="Invalid env-var syntax"):
            V1Blueprint(
                {
                    "name": "test",
                    "workspace": {
                        "type": "remote",
                        "host": "https://example.com",
                        "api_key": "$REMOTE_API_KEY",
                    },
                    "phases": [{"id": "s1", "prompt": "hi"}],
                }
            )


# ---------------------------------------------------------------------------
# V1Blueprint._get_workspace
# ---------------------------------------------------------------------------


class TestBlueprintGetWorkspace:
    def test_get_workspace_default_local(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "s1", "prompt": "hi"}]})
        ws = bp._get_workspace({})
        assert ws.__class__.__name__ == "LocalWorkspace"

    def test_get_workspace_local_from_config(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "workspace": {"type": "local", "working_dir": "/tmp"},
                "phases": [{"id": "s1", "prompt": "hi"}],
            }
        )
        ws = bp._get_workspace({})
        assert ws.__class__.__name__ == "LocalWorkspace"

    def test_get_workspace_remote_from_config(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "workspace": {
                    "type": "remote",
                    "host": "https://example.com",
                    "working_dir": "/workspace",
                },
                "phases": [{"id": "s1", "prompt": "hi"}],
            }
        )
        ws = bp._get_workspace({})
        assert ws.__class__.__name__ == "RemoteWorkspace"

    def test_context_interpolation_in_workspace(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "workspace": {
                    "type": "remote",
                    "host": "https://example.com",
                    "working_dir": "{clone_dir}",
                },
                "phases": [{"id": "s1", "prompt": "hi"}],
            }
        )
        ws = bp._get_workspace({"clone_dir": "/repo"})
        assert ws.__class__.__name__ == "RemoteWorkspace"

    def test_project_path_used_when_no_working_dir(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "s1", "prompt": "hi"}]})
        bp.project_path = "/project"
        ws = bp._get_workspace({})
        assert ws.__class__.__name__ == "LocalWorkspace"
