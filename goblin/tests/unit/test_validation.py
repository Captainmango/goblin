"""Tests for blueprint JSON Schema validation (goblin/validation.py) and the CLI validate command."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from goblin.cli import cli
from goblin.runner import BlueprintRunner
from goblin.validation import format_errors, validate_blueprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_blueprint(tmp_path, data: dict[str, Any]) -> str:
    path = tmp_path / "bp.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


# ---------------------------------------------------------------------------
# validate_blueprint
# ---------------------------------------------------------------------------


class TestValidateBlueprint:
    def test_valid_blueprint_passes(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert errors == []

    def test_missing_required_version(self, tmp_path):
        bp = {
            "blueprint": {
                "name": "test",
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["validator"] == "required" for e in errors)

    def test_missing_required_blueprint_name(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any("name" in e["message"] for e in errors)

    def test_wrong_type(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": "not-a-list",
            },
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["validator"] == "type" for e in errors)

    def test_extra_property(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
            "extra_field": 123,
        }
        path = _write_blueprint(tmp_path, bp)
        errors = validate_blueprint(path)
        assert any(e["validator"] == "additionalProperties" for e in errors)

    def test_invalid_yaml_returns_error(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("")
        errors = validate_blueprint(str(path))
        assert errors == [
            {
                "path": "",
                "message": "File is empty or not valid YAML",
                "validator": "parse",
                "severity": "error",
            }
        ]


# ---------------------------------------------------------------------------
# format_errors
# ---------------------------------------------------------------------------


class TestFormatErrors:
    def test_empty_list_returns_empty_string(self):
        assert format_errors([]) == ""

    def test_formats_error_lines(self):
        errors = [
            {
                "path": "blueprint.name",
                "message": "is a required property",
                "validator": "required",
                "severity": "error",
            },
            {
                "path": "blueprint.phases[0].id",
                "message": "is not of type 'string'",
                "validator": "type",
                "severity": "error",
            },
        ]
        text = format_errors(errors)
        assert "blueprint.name: is a required property" in text
        assert "blueprint.phases[0].id: is not of type 'string'" in text

    def test_formats_warning_lines(self):
        errors = [
            {
                "path": "blueprint.phases[0].harness.command",
                "message": "overrides the pinned command",
                "validator": "semantic",
                "severity": "warning",
            },
        ]
        text = format_errors(errors)
        assert "(warning)" in text
        assert "overrides the pinned command" in text


# ---------------------------------------------------------------------------
# CLI validate command
# ---------------------------------------------------------------------------


class TestCliValidate:
    def test_valid_blueprint_prints_valid(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", path])
        assert result.exit_code == 0
        assert f"{path}: valid" in result.output

    def test_invalid_blueprint_prints_errors_and_exits_one(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", path])
        assert result.exit_code == 1
        assert f"{path}: invalid" in result.output
        assert "is a required property" in result.output

    def test_multiple_files_mixed(self, tmp_path):
        valid_bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        invalid_bp = {
            "version": "v1",
            "blueprint": {
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        valid_path = tmp_path / "valid.yaml"
        invalid_path = tmp_path / "invalid.yaml"
        valid_path.write_text(yaml.safe_dump(valid_bp))
        invalid_path.write_text(yaml.safe_dump(invalid_bp))

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(valid_path), str(invalid_path)])
        assert result.exit_code == 1
        assert f"{valid_path}: valid" in result.output
        assert f"{invalid_path}: invalid" in result.output


# ---------------------------------------------------------------------------
# BlueprintRunner fail-fast validation
# ---------------------------------------------------------------------------


class TestRunnerValidation:
    def test_runner_raises_on_invalid_blueprint(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        with pytest.raises(ValueError, match="failed schema validation"):
            BlueprintRunner(path)

    def test_runner_error_references_validate_command(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "phases": [{"id": "p1", "prompt": "hello"}],
            },
        }
        path = _write_blueprint(tmp_path, bp)
        with pytest.raises(ValueError, match="goblin validate"):
            BlueprintRunner(path)
