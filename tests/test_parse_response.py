"""Tests for PromptStep._parse_response — JSON repair loop."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.step import PromptStep


def _make_prompt_step() -> PromptStep:
    blueprint = MagicMock()
    blueprint._build_hooks.return_value = []
    blueprint._build_mcp_servers.return_value = {}
    blueprint.project_path = None
    blueprint.project_settings_path = None
    blueprint.project_agents = None
    raw = {"id": "test_step", "prompt": "do something"}
    return PromptStep(raw, blueprint)


class TestParseResponse:
    @pytest.mark.asyncio
    async def test_valid_json_returns_immediately(self):
        step = _make_prompt_step()
        result = await step._parse_response('{"success": true, "message": ["ok"]}')
        assert result == {"success": True, "message": ["ok"]}

    @pytest.mark.asyncio
    async def test_repair_is_called_on_broken_json(self):
        step = _make_prompt_step()
        repaired = '{"success": true, "message": ["repaired"]}'
        step._request_json_repair = AsyncMock(return_value=repaired)

        result = await step._parse_response("not valid json")

        step._request_json_repair.assert_called_once_with("not valid json")
        assert result == {"success": True, "message": ["repaired"]}

    @pytest.mark.asyncio
    async def test_repaired_text_is_used_on_next_attempt(self):
        """Verify the fix: repaired text (not original) is parsed on retry."""
        step = _make_prompt_step()
        broken = "not valid json"
        repaired = '{"success": true, "message": ["fixed"]}'

        call_count = 0

        async def repair_side_effect(text: str) -> str:
            nonlocal call_count
            call_count += 1
            # Should receive the original broken text, return valid JSON
            assert text == broken
            return repaired

        step._request_json_repair = repair_side_effect

        result = await step._parse_response(broken)

        assert call_count == 1
        assert result["success"] is True
        assert result["message"] == ["fixed"]

    @pytest.mark.asyncio
    async def test_repair_called_with_updated_text_on_second_failure(self):
        """Each repair call receives the most recently repaired text, not the original."""
        step = _make_prompt_step()
        first_repaired = "still broken"
        second_repaired = '{"success": false, "message": ["second repair"]}'

        step._request_json_repair = AsyncMock(side_effect=[first_repaired, second_repaired])

        result = await step._parse_response("original broken")

        assert step._request_json_repair.call_count == 2
        # Second call must receive the first repaired text, not the original
        assert step._request_json_repair.call_args_list[1].args[0] == first_repaired
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self):
        step = _make_prompt_step()
        step._request_json_repair = AsyncMock(return_value="still broken")

        with pytest.raises(json.JSONDecodeError):
            await step._parse_response("broken json", retries=2)

        assert step._request_json_repair.call_count == 2


class TestRunEmptyResponse:
    @pytest.mark.asyncio
    async def test_no_text_blocks_returns_failure(self):
        """PromptStep.run() returns a structured failure when agent produces no text."""
        step = _make_prompt_step()

        async def empty_query(*args, **kwargs):
            return
            yield  # make it an async generator

        with patch("goblin.step.query", side_effect=empty_query):
            result = await step.run({})

        assert result == {"success": False, "message": ["Agent produced no text output"]}

    @pytest.mark.asyncio
    async def test_assistant_message_with_no_text_blocks_returns_failure(self):
        """AssistantMessage with only non-text blocks still triggers the empty guard."""
        from claude_agent_sdk import AssistantMessage

        step = _make_prompt_step()

        msg = MagicMock(spec=AssistantMessage)
        msg.content = [MagicMock(spec=[])]  # block without .text attribute

        async def query_no_text(*args, **kwargs):
            yield msg

        with patch("goblin.step.query", side_effect=query_no_text):
            result = await step.run({})

        assert result == {"success": False, "message": ["Agent produced no text output"]}
