"""Live ACP harness integration tests.

These tests exercise the ACP harness against a real opencode subprocess.  They
are kept in the integration suite so that the unit tests remain fast and do not
require external tooling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goblin.blueprint import V1Blueprint
from goblin_common.testing.vcr import use_cassette

OPENCODE_ACP_HARNESS = {"type": "acp", "server": "custom", "command": ["opencode", "acp"], "prompt_timeout": 300}

CASSETTES_DIR = Path(__file__).parent / "cassettes"

@pytest.mark.asyncio
async def test_acp_phase_runs_real_opencode(tmp_path):
    bp = V1Blueprint(
        {
            "name": "test_bp",
            "phases": [
                {
                    "id": "p1",
                    "prompt": "Read README.md and say one sentence about it.",
                    "harness": OPENCODE_ACP_HARNESS,
                }
            ],
        }
    )
    phase = bp.phases[0]

    readme = tmp_path / "README.md"
    readme.write_text("# Goblin test repo\nThis repo tests the ACP harness.")

    cassette_path = CASSETTES_DIR / "test_acp_phase_runs_real_opencode.b64"

    with use_cassette(cassette_path, record_mode="once"):
        # Override working_dir so the harness sees our temp repo.
        bp._workspace_config = {"type": "local", "working_dir": str(tmp_path)}

        result = await phase.run({})

        assert result["success"] is True
        assert result["output"]
        assert result["message"]


@pytest.mark.asyncio
async def test_acp_phase_max_iterations_breaker_trips(tmp_path):
    bp = V1Blueprint(
        {
            "name": "test_bp",
            "phases": [
                {
                    "id": "p1",
                    "prompt": "Write a short greeting in README.md.",
                    "harness": OPENCODE_ACP_HARNESS,
                    "max_iterations": 1,
                }
            ],
        }
    )
    phase = bp.phases[0]

    bp._workspace_config = {"type": "local", "working_dir": str(tmp_path)}

    cassette_path = CASSETTES_DIR / "test_acp_phase_max_iterations_breaker_trips.b64"

    with use_cassette(cassette_path, record_mode="once"):
        result = await phase.run({})

        assert result["success"] is False
        assert "max_iterations" in result["message"][0]


@pytest.mark.asyncio
async def test_acp_phase_uses_forwarded_mcp_server(tmp_path):
    facts = tmp_path / "facts.txt"
    facts.write_text("goblin colour: green")

    bp = V1Blueprint(
        {
            "name": "test_bp",
            "phases": [
                {
                    "id": "p1",
                    "prompt": "Read facts.txt and report the goblin colour.",
                    "harness": OPENCODE_ACP_HARNESS,
                    "mcp_servers": {
                        "filesystem": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem", str(tmp_path)],
                        }
                    },
                }
            ],
        }
    )
    phase = bp.phases[0]

    bp._workspace_config = {"type": "local", "working_dir": str(tmp_path)}

    cassette_path = CASSETTES_DIR / "test_acp_phase_uses_forwarded_mcp_server.b64"

    with use_cassette(cassette_path, record_mode="once"):
        result = await phase.run({})

        assert result["success"] is True
        assert "green" in result["output"].lower()
