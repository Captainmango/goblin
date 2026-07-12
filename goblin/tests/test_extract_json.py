"""Tests for goblin.phase._extract_json — JSON extraction from agent responses."""

from __future__ import annotations

import json

import pytest

from goblin.phase import _extract_json


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain_json_string(self):
        result = _extract_json('{"success": true, "message": ["done"]}')
        assert result == {"success": True, "message": ["done"]}

    def test_json_in_markdown_code_block(self):
        text = '```\n{"success": true, "message": ["ok"]}\n```'
        result = _extract_json(text)
        assert result == {"success": True, "message": ["ok"]}

    def test_json_in_code_block_with_lang_tag(self):
        text = '```json\n{"success": false, "message": ["error"]}\n```'
        result = _extract_json(text)
        assert result == {"success": False, "message": ["error"]}

    def test_json_with_surrounding_whitespace(self):
        text = '  \n  {"success": true, "message": ["ok"]}  \n  '
        result = _extract_json(text)
        assert result == {"success": True, "message": ["ok"]}

    def test_code_block_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n{"success": true, "message": ["done"]}\n```\nDone.'
        result = _extract_json(text)
        assert result == {"success": True, "message": ["done"]}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not valid json")

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("")

    def test_code_block_with_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("```json\nnot json\n```")
