"""Tests for CircuitBreaker, SpendTracker, and LimitExceeded."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from goblin.cost_control import CircuitBreaker, LimitExceeded, SpendTracker


# ---------------------------------------------------------------------------
# SpendTracker
# ---------------------------------------------------------------------------


class TestSpendTracker:
    def test_initial_values(self):
        t = SpendTracker()
        assert t.iterations == 0
        assert t.tokens == 0
        assert t.cost == 0.0

    def test_absorb_increments_iterations(self):
        t = SpendTracker()
        conversation = MagicMock()
        conversation.conversation_stats = None
        t.absorb(conversation)
        assert t.iterations == 1

    def test_absorb_adds_tokens(self):
        t = SpendTracker()
        conversation = MagicMock()

        token_usage = MagicMock()
        token_usage.prompt_tokens = 100
        token_usage.completion_tokens = 50

        metrics = MagicMock()
        metrics.accumulated_token_usage = token_usage
        metrics.accumulated_cost = 0.25

        stats = MagicMock()
        stats.get_combined_metrics.return_value = metrics
        conversation.conversation_stats = stats

        t.absorb(conversation)
        assert t.tokens == 150
        assert t.cost == 0.25
        assert t.iterations == 1

    def test_absorb_handles_none_token_usage(self):
        t = SpendTracker()
        conversation = MagicMock()

        metrics = MagicMock()
        metrics.accumulated_token_usage = None
        metrics.accumulated_cost = 0.1

        stats = MagicMock()
        stats.get_combined_metrics.return_value = metrics
        conversation.conversation_stats = stats

        t.absorb(conversation)
        assert t.tokens == 0
        assert t.cost == 0.1

    def test_absorb_handles_none_cost(self):
        t = SpendTracker()
        conversation = MagicMock()

        token_usage = MagicMock()
        token_usage.prompt_tokens = 10
        token_usage.completion_tokens = 5

        metrics = MagicMock()
        metrics.accumulated_token_usage = token_usage
        metrics.accumulated_cost = None

        stats = MagicMock()
        stats.get_combined_metrics.return_value = metrics
        conversation.conversation_stats = stats

        t.absorb(conversation)
        assert t.tokens == 15
        assert t.cost == 0.0

    def test_multiple_absorbs_accumulate(self):
        t = SpendTracker()
        for i in range(3):
            conversation = MagicMock()
            token_usage = MagicMock()
            token_usage.prompt_tokens = 10
            token_usage.completion_tokens = 5

            metrics = MagicMock()
            metrics.accumulated_token_usage = token_usage
            metrics.accumulated_cost = 0.1 * (i + 1)

            stats = MagicMock()
            stats.get_combined_metrics.return_value = metrics
            conversation.conversation_stats = stats
            t.absorb(conversation)

        assert t.iterations == 3
        assert t.tokens == 45
        assert t.cost == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_check_passes_when_no_limits(self):
        cb = CircuitBreaker()
        cb.check()  # should not raise

    def test_check_raises_on_max_iterations(self):
        cb = CircuitBreaker(max_iterations=2)
        cb.tracker.iterations = 2
        with pytest.raises(LimitExceeded) as exc_info:
            cb.check()
        assert exc_info.value.reason == "max_iterations"
        assert exc_info.value.tracker.iterations == 2

    def test_check_raises_on_max_tokens(self):
        cb = CircuitBreaker(max_tokens=100)
        cb.tracker.tokens = 100
        with pytest.raises(LimitExceeded) as exc_info:
            cb.check()
        assert exc_info.value.reason == "max_tokens"

    def test_check_raises_on_max_cost(self):
        cb = CircuitBreaker(max_cost=1.0)
        cb.tracker.cost = 1.0
        with pytest.raises(LimitExceeded) as exc_info:
            cb.check()
        assert exc_info.value.reason == "max_cost"

    def test_check_passes_when_under_limit(self):
        cb = CircuitBreaker(max_iterations=5, max_tokens=100, max_cost=1.0)
        cb.tracker.iterations = 4
        cb.tracker.tokens = 99
        cb.tracker.cost = 0.99
        cb.check()  # should not raise

    def test_absorb_updates_tracker(self):
        cb = CircuitBreaker()
        conversation = MagicMock()
        conversation.conversation_stats = None
        cb.absorb(conversation)
        assert cb.tracker.iterations == 1

    def test_absorb_raises_when_limit_exceeded(self):
        cb = CircuitBreaker(max_iterations=2)
        conversation = MagicMock()
        conversation.conversation_stats = None
        cb.absorb(conversation)
        assert cb.tracker.iterations == 1
        with pytest.raises(LimitExceeded):
            cb.absorb(conversation)

    def test_absorb_counts_prompt_and_completion_tokens(self):
        cb = CircuitBreaker(max_tokens=51)
        conversation = MagicMock()

        token_usage = MagicMock()
        token_usage.prompt_tokens = 30
        token_usage.completion_tokens = 20

        metrics = MagicMock()
        metrics.accumulated_token_usage = token_usage
        metrics.accumulated_cost = 0.0

        stats = MagicMock()
        stats.get_combined_metrics.return_value = metrics
        conversation.conversation_stats = stats

        cb.absorb(conversation)
        assert cb.tracker.tokens == 50
        with pytest.raises(LimitExceeded):
            cb.absorb(conversation)
