import pytest

from goblin.utils.config import reset_config


@pytest.fixture(autouse=True)
def _reset_goblin_config():
    """Reset the central config singleton before each test.

    This ensures tests that monkeypatch environment variables see the updated
    values when the code under test calls :func:`goblin.utils.config.get_config`.
    """
    reset_config()
    yield
