"""Golden-path integration tests that run real blueprints end-to-end.

These tests exercise the full :class:`BlueprintRunner` stack including blueprint
loading, validation, orchestration, and phase execution.  LLM calls are recorded
with :mod:`vcr` so the suite remains deterministic and does not require API keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goblin.output_schema import reset_output_schema_cache
from goblin.runner import BlueprintRunner
from goblin.utils.config import reset_config
from goblin_common.testing.vcr import use_cassette

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_PATH = REPO_ROOT / "examples" / "basic"
ACP_EXAMPLE_PATH = REPO_ROOT / "examples" / "acp_example.yaml"
CASSETTES_DIR = Path(__file__).parent / "cassettes"
CASSETTE_PATH = CASSETTES_DIR / "test_runs_structured_output_example_end_to_end.b64"


@pytest.fixture(autouse=True)
def _reset_goblin_config():
    """Reset the central config singleton before each test."""
    reset_config()
    yield


@pytest.fixture(autouse=True)
def _reset_output_schema_cache():
    """Clear the inline-schema cache and registry before each test."""
    reset_output_schema_cache()
    yield


class TestBasicExamplesGoldenPath:
    @pytest.mark.asyncio
    async def test_runs_structured_output_example_end_to_end(self, tmp_path):
        """The structured_output example blueprint loads and runs both phases."""
        blueprint_file = EXAMPLES_PATH / "structured_output_example.yaml"
        assert blueprint_file.is_file(), "example blueprint must exist"

        with use_cassette(CASSETTE_PATH, record_mode="once"):
            runner = BlueprintRunner(str(blueprint_file), run_dir=str(tmp_path))
            result = await runner.run(context={"input": "What colour is the sky?"})
            assert result is not None
            assert result["classify_intent"]["success"] is True
            assert result["summarise"]["success"] is True
            assert len(result) == 2


class TestAcpExampleGoldenPath:
    @pytest.mark.asyncio
    async def test_runs_acp_example_end_to_end(self, tmp_path):
        """The ACP example blueprint loads and runs both phases against opencode."""
        assert ACP_EXAMPLE_PATH.is_file(), "ACP example blueprint must exist"

        api_dir = tmp_path / "api"
        ui_dir = tmp_path / "ui"
        api_dir.mkdir()
        ui_dir.mkdir()

        runner = BlueprintRunner(
            str(ACP_EXAMPLE_PATH),
            run_dir=str(tmp_path),
        )
        result = await runner.run(context={"api_dir": str(api_dir), "ui_dir": str(ui_dir)})
        assert result is not None
        assert result["api_change"]["success"] is True
        assert result["ui_change"]["success"] is True
        assert len(result) == 2

        version_file = api_dir / "version.txt"
        status_file = ui_dir / "status.txt"
        assert version_file.read_text().strip() == "1.0.0"
        assert "1.0.0" in status_file.read_text()
