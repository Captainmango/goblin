# Testing

## Running the Test Suite

Run all tests from the repo root:

```sh
task test
```

Run tests for a single project:

```sh
task goblin:test
task notifications-relay:test
```

## Test Structure

Tests live in `<project>/tests/` and use pytest with `pytest-asyncio` for async tests.

```
goblin/tests/
├── conftest.py          # Shared fixtures
├── tools/               # Tool executors
├── utils/               # Utility modules
├── notifications/       # Notification client/config
├── test_validation.py   # Blueprint schema validation
├── test_blueprint_run.py  # Blueprint orchestration
├── test_swarm.py        # Swarm orchestration
├── test_checkpoint.py   # Checkpoint resume
└── ...
```

## Writing Blueprint Tests

Test blueprints by constructing them directly:

```python
import pytest
from goblin.blueprint import V1Blueprint

@pytest.mark.asyncio
async def test_simple_blueprint():
    spec = {
        "version": "v1",
        "blueprint": {
            "name": "test",
            "phases": [
                {
                    "id": "greet",
                    "prompt": "Say hello",
                    "allowed_tools": [],
                }
            ]
        }
    }
    blueprint = V1Blueprint(spec)
    results = await blueprint.run()
    assert results["greet"]["success"] is True
```

## Mock Fixtures

Common mocks are defined in `conftest.py`:

```python
@pytest.fixture
def mock_llm(monkeypatch):
    """Mock the LLM builder to return a fake LLM."""
    ...

@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory with .claude/settings.json."""
    ...
```

## Testing Checkpoints

```python
from goblin.checkpoint import Checkpoint

@pytest.mark.asyncio
async def test_checkpoint(tmp_path):
    cp = Checkpoint(
        run_dir=str(tmp_path),
        run_id="test",
        blueprint_path="test.yaml",
        context={},
    )
    await cp.phase_started("p1", {"context": {}})
    await cp.phase_completed("p1", {"success": True})
    assert "p1" in cp._data["phases"]
```

## Testing Swarms

Swarm tests often mock the database or use the SQLite backend:

```python
@pytest.mark.asyncio
async def test_swarm_phase():
    spec = {
        "id": "swarm1",
        "swarm": {
            "goal": "Test goal",
            "coordinator": {"prompt": "Plan"},
            "agents": [
                {"name": "a1", "prompt": "Work"}
            ],
            "backend": "sqlite",
            "cleanup": "never",
        }
    }
    ...
```
