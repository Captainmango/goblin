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
EXAMPLES_PATH = REPO_ROOT / "examples"
CASSETTES_DIR = Path(__file__).parent / "cassettes"


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


class TestStructuredOutputExample:
    @pytest.mark.asyncio
    async def test_runs_structured_output_example_end_to_end(self, tmp_path):
        """The structured_output example blueprint loads and runs both phases."""
        blueprint_file = EXAMPLES_PATH / "structured_output_example.yaml"
        assert blueprint_file.is_file(), "example blueprint must exist"

        cassette_path = CASSETTES_DIR / "test_runs_structured_output_example_end_to_end.b64"
        with use_cassette(cassette_path, record_mode="once"):
            runner = BlueprintRunner(str(blueprint_file), run_dir=str(tmp_path))
            result = await runner.run(context={"input": "What colour is the sky?"})
            assert result is not None
            assert result["classify_intent"]["success"] is True
            assert result["summarise"]["success"] is True
            assert len(result) == 2
