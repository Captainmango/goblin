"""Tests for goblin.utils.jinja_helpers — filter_contains and filter_json_value."""

from __future__ import annotations

import pytest

from goblin.utils.jinja_helpers import filter_contains, filter_json_value


# ---------------------------------------------------------------------------
# filter_contains
# ---------------------------------------------------------------------------

class TestFilterContains:
    def test_match_in_single_message(self):
        assert filter_contains(["hello world"], "world") is True

    def test_no_match(self):
        assert filter_contains(["hello world"], "missing") is False

    def test_match_in_second_message(self):
        assert filter_contains(["first", "second has target"], "target") is True

    def test_empty_list(self):
        assert filter_contains([], "anything") is False

    def test_non_string_items_skipped(self):
        assert filter_contains([42, None, {"key": "val"}], "key") is False

    def test_exact_match(self):
        assert filter_contains(["exact"], "exact") is True

    def test_substring_match(self):
        assert filter_contains(["abcdef"], "cde") is True

    def test_empty_search_string_matches(self):
        assert filter_contains(["anything"], "") is True


# ---------------------------------------------------------------------------
# filter_json_value
# ---------------------------------------------------------------------------

class TestFilterJsonValue:
    def test_extracts_top_level_key(self):
        result = filter_json_value(['{"status": "ok"}'], "status")
        assert result == "ok"

    def test_extracts_nested_key(self):
        result = filter_json_value(['{"data": {"status": "ok"}}'], "data.status")
        assert result == "ok"

    def test_extracts_deeply_nested_key(self):
        result = filter_json_value(['{"a": {"b": {"c": 42}}}'], "a.b.c")
        assert result == 42

    def test_missing_key_returns_none(self):
        result = filter_json_value(['{"data": {}}'], "data.missing")
        assert result is None

    def test_completely_missing_path_returns_none(self):
        result = filter_json_value(['{"foo": "bar"}'], "nonexistent.path")
        assert result is None

    def test_first_matching_message_wins(self):
        messages = [
            '{"status": "first"}',
            '{"status": "second"}',
        ]
        result = filter_json_value(messages, "status")
        assert result == "first"

    def test_skips_non_json_messages(self):
        messages = [
            "not json",
            '{"status": "ok"}',
        ]
        result = filter_json_value(messages, "status")
        assert result == "ok"

    def test_skips_non_string_messages(self):
        messages = [42, None, '{"status": "ok"}']
        result = filter_json_value(messages, "status")
        assert result == "ok"

    def test_empty_list_returns_none(self):
        result = filter_json_value([], "any.key")
        assert result is None

    def test_json_array_skipped(self):
        result = filter_json_value(['[1, 2, 3]'], "0")
        assert result is None

    def test_boolean_value_extracted(self):
        result = filter_json_value(['{"active": true}'], "active")
        assert result is True

    def test_numeric_value_extracted(self):
        result = filter_json_value(['{"count": 7}'], "count")
        assert result == 7
