"""Tests for V1Blueprint._evaluate_condition — Jinja2 condition evaluation
with custom filters (contains, json_value) and edge cases."""

from __future__ import annotations

from goblin.blueprint import V1Blueprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint() -> V1Blueprint:
    return V1Blueprint({"name": "test_bp", "phases": [{"id": "s1", "prompt": "hi"}]})


# ---------------------------------------------------------------------------
# Basic truthy / falsy
# ---------------------------------------------------------------------------


class TestEvaluateConditionBasic:
    def test_true_boolean(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.success }}", {"a": {"success": True}}) is True

    def test_false_boolean(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.success }}", {"a": {"success": False}}) is False

    def test_none_is_falsy(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.value }}", {"a": {"value": None}}) is False

    def test_zero_is_falsy(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.count }}", {"a": {"count": 0}}) is False

    def test_nonzero_integer_is_truthy(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.count }}", {"a": {"count": 5}}) is True

    def test_nonempty_string_is_truthy(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.msg }}", {"a": {"msg": "hello"}}) is True

    def test_empty_string_is_falsy(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("{{ steps.a.msg }}", {"a": {"msg": ""}}) is False


# ---------------------------------------------------------------------------
# Bare expression (no {{ }}) — auto-wrapped
# ---------------------------------------------------------------------------


class TestEvaluateConditionAutoWrap:
    def test_bare_truthy_expression(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("steps.a.success", {"a": {"success": True}}) is True

    def test_bare_falsy_expression(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("steps.a.success", {"a": {"success": False}}) is False

    def test_whitespace_stripped(self):
        bp = _make_blueprint()
        assert bp._evaluate_condition("  {{ steps.a.success }}  ", {"a": {"success": True}}) is True


# ---------------------------------------------------------------------------
# contains filter
# ---------------------------------------------------------------------------


class TestEvaluateConditionContains:
    def test_contains_matching_substring(self):
        bp = _make_blueprint()
        results = {"clone": {"message": ["repo cloned successfully"]}}
        assert bp._evaluate_condition("{{ steps.clone.message | contains('cloned successfully') }}", results) is True

    def test_contains_no_match(self):
        bp = _make_blueprint()
        results = {"clone": {"message": ["repo downloaded"]}}
        assert bp._evaluate_condition("{{ steps.clone.message | contains('cloned successfully') }}", results) is False

    def test_contains_checks_all_messages(self):
        bp = _make_blueprint()
        results = {"step1": {"message": ["first", "second has keyword", "third"]}}
        assert bp._evaluate_condition("{{ steps.step1.message | contains('keyword') }}", results) is True

    def test_contains_empty_message_list(self):
        bp = _make_blueprint()
        results = {"step1": {"message": []}}
        assert bp._evaluate_condition("{{ steps.step1.message | contains('anything') }}", results) is False


# ---------------------------------------------------------------------------
# json_value filter
# ---------------------------------------------------------------------------


class TestEvaluateConditionJsonValue:
    def test_json_value_extracts_value(self):
        bp = _make_blueprint()
        results = {"api": {"message": ['{"data": {"status": "ok"}}']}}
        assert bp._evaluate_condition("{{ steps.api.message | json_value('data.status') == 'ok' }}", results) is True

    def test_json_value_mismatch(self):
        bp = _make_blueprint()
        results = {"api": {"message": ['{"data": {"status": "error"}}']}}
        assert bp._evaluate_condition("{{ steps.api.message | json_value('data.status') == 'ok' }}", results) is False

    def test_json_value_missing_key_returns_none(self):
        bp = _make_blueprint()
        results = {"api": {"message": ['{"data": {}}']}}
        assert bp._evaluate_condition("{{ steps.api.message | json_value('data.missing') }}", results) is False

    def test_json_value_with_nested_path(self):
        bp = _make_blueprint()
        results = {"api": {"message": ['{"a": {"b": {"c": 42}}}']}}
        assert bp._evaluate_condition("{{ steps.api.message | json_value('a.b.c') == 42 }}", results) is True
