from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from goblin.output_schema import reset_output_schema_cache
from goblin.utils.config import reset_config


@pytest.fixture
def mock_conversation():
    """Return a conversation mock with populated spend stats."""
    conversation = MagicMock()
    conversation.arun = AsyncMock()
    conversation.run = MagicMock()
    conversation.state.events = []
    stats = MagicMock()
    metrics = MagicMock()
    token_usage = MagicMock()
    token_usage.prompt_tokens = 100
    token_usage.completion_tokens = 50
    metrics.accumulated_token_usage = token_usage
    metrics.accumulated_cost = 0.1
    stats.get_combined_metrics.return_value = metrics
    conversation.conversation_stats = stats
    return conversation


@pytest.fixture(autouse=True)
def _reset_goblin_config():
    """Reset the central config singleton before each test.

    This ensures tests that monkeypatch environment variables see the updated
    values when the code under test calls :func:`goblin.utils.config.get_config`.
    """
    reset_config()
    yield


@pytest.fixture(autouse=True)
def _suppress_dotenv_loading(monkeypatch):
    """Prevent :func:`load_dotenv_from_cwd` from re-reading a repo-root ``.env``.

    Without this, tests that delete env vars can see them resurrected when the
    config singleton loads ``.env`` from the current working directory. Patching
    the loader to a no-op makes the test suite hermetic regardless of where
    pytest is invoked from.
    """
    monkeypatch.setattr("goblin.utils.config.load_dotenv_from_cwd", lambda: None)


@pytest.fixture(autouse=True)
def _reset_output_schema_cache():
    """Clear the inline-schema cache and registry before each test.

    Without this, generated inline models from an earlier test can shadow
    later tests that reuse the same blueprint/phase id with a different
    schema.
    """
    reset_output_schema_cache()
    yield
