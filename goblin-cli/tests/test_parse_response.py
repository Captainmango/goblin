"""Tests for PromptPhase._parse_response — JSON repair loop."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from goblin_cli.phase import PromptPhase


def _make_prompt_phase() -> PromptPhase:
    blueprint = MagicMock()
    blueprint._build_hooks.return_value = None
    blueprint._build_mcp_servers.return_value = {}
    blueprint.project_path = None
    blueprint.project_refs = ""
    blueprint.project_agents = {}
    raw = {"id": "test_phase", "prompt": "do something"}
    return PromptPhase(raw, blueprint)


class TestParseResponse:
    @pytest.mark.asyncio
    async def test_valid_json_returns_immediately(self):
        phase = _make_prompt_phase()
        result = await phase._parse_response('{"success": true, "message": ["ok"]}')
        assert result == {"success": True, "message": ["ok"]}

    @pytest.mark.asyncio
    async def test_repair_is_called_on_broken_json(self):
        phase = _make_prompt_phase()
        repaired = '{"success": true, "message": ["repaired"]}'
        phase._request_json_repair = AsyncMock(return_value=repaired)

        result = await phase._parse_response("not valid json")

        phase._request_json_repair.assert_called_once_with("not valid json")
        assert result == {"success": True, "message": ["repaired"]}

    @pytest.mark.asyncio
    async def test_repaired_text_is_used_on_next_attempt(self):
        """Verify the fix: repaired text (not original) is parsed on retry."""
        phase = _make_prompt_phase()
        broken = "not valid json"
        repaired = '{"success": true, "message": ["fixed"]}'

        call_count = 0

        async def repair_side_effect(text: str) -> str:
            nonlocal call_count
            call_count += 1
            # Should receive the original broken text, return valid JSON
            assert text == broken
            return repaired

        phase._request_json_repair = repair_side_effect

        result = await phase._parse_response(broken)

        assert call_count == 1
        assert result["success"] is True
        assert result["message"] == ["fixed"]

    @pytest.mark.asyncio
    async def test_repair_called_with_updated_text_on_second_failure(self):
        """Each repair call receives the most recently repaired text, not the original."""
        phase = _make_prompt_phase()
        first_repaired = "still broken"
        second_repaired = '{"success": false, "message": ["second repair"]}'

        phase._request_json_repair = AsyncMock(side_effect=[first_repaired, second_repaired])

        result = await phase._parse_response("original broken")

        assert phase._request_json_repair.call_count == 2
        # Second call must receive the first repaired text, not the original
        assert phase._request_json_repair.call_args_list[1].args[0] == first_repaired
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self):
        phase = _make_prompt_phase()
        phase._request_json_repair = AsyncMock(return_value="still broken")

        with pytest.raises(json.JSONDecodeError):
            await phase._parse_response("broken json", retries=2)

        assert phase._request_json_repair.call_count == 2
