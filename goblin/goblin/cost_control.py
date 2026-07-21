from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SpendTracker:
    iterations: int = 0
    tokens: int = 0
    cost: float = 0.0

    def absorb(self, conversation) -> None:
        """Read stats from a finished LocalConversation."""
        stats = getattr(conversation, "conversation_stats", None)
        if stats is not None:
            metrics = stats.get_combined_metrics()
            token_usage = getattr(metrics, "accumulated_token_usage", None)
            if token_usage is not None:
                prompt_tokens = getattr(token_usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(token_usage, "completion_tokens", 0) or 0
                self.tokens += prompt_tokens + completion_tokens
            cost = getattr(metrics, "accumulated_cost", 0.0) or 0.0
            self.cost += cost

        # Each arun() call counts as one iteration against the breaker limit.
        # TODO: switch to fine-grained event counting if needed.
        self.iterations += 1


class GoblinError(Exception):
    """Base exception for the goblin project."""


class LimitExceeded(GoblinError):
    def __init__(self, reason: str, tracker: SpendTracker):
        super().__init__(reason)
        self.reason = reason
        self.tracker = tracker


class CircuitBreaker:
    def __init__(
        self,
        max_iterations: int | None = None,
        max_tokens: int | None = None,
        max_cost: float | None = None,
    ):
        self.tracker = SpendTracker()
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.max_cost = max_cost

    def check(self) -> None:
        """Raise LimitExceeded if any limit is breached."""
        t = self.tracker
        if self.max_iterations is not None and t.iterations >= self.max_iterations:
            raise LimitExceeded("max_iterations", t)
        if self.max_tokens is not None and t.tokens >= self.max_tokens:
            raise LimitExceeded("max_tokens", t)
        if self.max_cost is not None and t.cost >= self.max_cost:
            raise LimitExceeded("max_cost", t)

    def absorb(self, conversation) -> None:
        self.tracker.absorb(conversation)
        self.check()
