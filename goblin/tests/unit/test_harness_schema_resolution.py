"""Tests for the ACP harness schema and blueprint resolution."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase
from goblin.validation import validate_blueprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_blueprint(tmp_path, data: dict[str, Any]) -> str:
    path = tmp_path / "bp.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestHarnessSchemaValidation:
    def test_defaults_and_phase_harness_validate(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "defaults": {
                    "harness": {
                        "type": "acp",
                        "server": "claude-code",
                        "prompt_timeout": 300,
                    }
                },
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hello",
                        "harness": {"working_dir": "{repo}"},
                    }
                ],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert errors == []

    def test_unknown_key_inside_harness_fails(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hello",
                        "harness": {"unknown_key": "value"},
                    }
                ],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["validator"] == "additionalProperties" for e in errors)

    def test_bad_server_enum_fails(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hello",
                        "harness": {"type": "acp", "server": "bad-provider"},
                    }
                ],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["validator"] == "enum" for e in errors)

    def test_openhands_type_validates(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [{"id": "p1", "prompt": "hello", "harness": {"type": "openhands"}}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert errors == []


# ---------------------------------------------------------------------------
# PromptPhase captures raw harness config
# ---------------------------------------------------------------------------


class TestPromptPhaseHarnessConfig:
    def test_harness_config_attribute_stored(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp", "server": "codex"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.harness_config == {"type": "acp", "server": "codex"}

    def test_harness_config_none_when_undeclared(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "p1", "prompt": "hi"}]})
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.harness_config is None


# ---------------------------------------------------------------------------
# resolve_harness_config
# ---------------------------------------------------------------------------


class TestResolveHarnessConfig:
    def test_undeclared_returns_none(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "p1", "prompt": "hi"}]})
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {}) is None

    def test_defaults_only(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"harness": {"type": "acp", "server": "claude-code"}},
                "phases": [{"id": "p1", "prompt": "hi"}],
            }
        )
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {}) == {
            "type": "acp",
            "server": "claude-code",
        }

    def test_phase_only(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp", "server": "codex"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {}) == {
            "type": "acp",
            "server": "codex",
        }

    def test_phase_overrides_defaults(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {
                    "harness": {
                        "type": "acp",
                        "server": "claude-code",
                        "model": "sonnet",
                    }
                },
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"server": "codex"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {}) == {
            "type": "acp",
            "server": "codex",
            "model": "sonnet",
        }

    def test_env_var_resolution(self, monkeypatch):
        monkeypatch.setenv("TEST_HARNESS_DIR", "/tmp/repo")
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"working_dir": "${TEST_HARNESS_DIR}"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {}) == {"working_dir": "/tmp/repo"}

    def test_missing_env_var_raises(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"working_dir": "${MISSING_HARNESS_DIR}"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        with pytest.raises(ValueError, match="MISSING_HARNESS_DIR"):
            bp.resolve_harness_config(phase, {})

    def test_context_interpolation(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"working_dir": "{clone_dir}"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {"clone_dir": "/workspace"}) == {"working_dir": "/workspace"}

    def test_interpolation_overrides_env_var(self, monkeypatch):
        """Both ${VAR} and {var} are resolved; interpolated context wins on collision."""
        monkeypatch.setenv("CLONE_DIR", "/env/repo")
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"working_dir": "{clone_dir}"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert bp.resolve_harness_config(phase, {"clone_dir": "/context/repo"}) == {"working_dir": "/context/repo"}

    def test_unbraced_env_var_syntax_rejected(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"working_dir": "$BAD_VAR"},
                    }
                ],
            }
        )
        phase = bp.phases[0]
        with pytest.raises(ValueError, match="BAD_VAR"):
            bp.resolve_harness_config(phase, {})

    def test_backwards_compatible_alias(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"harness": {"type": "acp"}},
                "phases": [{"id": "p1", "prompt": "hi"}],
            }
        )
        phase = bp.phases[0]
        assert bp._resolve_harness_config(phase, {}) == {"type": "acp"}
