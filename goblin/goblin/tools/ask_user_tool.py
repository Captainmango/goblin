"""Typed ask-the-user tool backed by the notifications relay.

Lets an agent pause and ask a human a question over Telegram (via
notifications-relay) instead of guessing, then blocks until a reply arrives
or the timeout elapses.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import Field

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

from goblin.notifications.client import make_client, resolve

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

ASK_USER_TIMEOUT_SECONDS = 15 * 60.0
DEFAULT_LABEL = "default"


class AskUserAction(Action):
    """Schema for asking the user a question."""

    question: str = Field(description="The question to send to the user.")


class AskUserObservation(Observation):
    """Result of asking the user a question."""

    answer: str | None = Field(default=None, description="The user's reply text, when one was received in time.")


TOOL_DESCRIPTION = """Ask the user a question and wait for their reply via the notifications relay (e.g. Telegram).
Use this when you need clarification or a decision from the user instead of guessing. Blocks for up to
15 minutes; if no reply arrives within that time an error is returned instead of an answer.
"""


class AskUserExecutor(ToolExecutor[AskUserAction, AskUserObservation]):
    """Sends a question through notifications-relay and polls for a reply.

    `token` is resolved once at construction time (label/token/env/local
    store, see `goblin.notifications.client.resolve`) so the executor
    survives conversation state snapshots without re-resolving pairing state
    on every call.
    """

    def __init__(self, token: str, timeout: float = ASK_USER_TIMEOUT_SECONDS):
        self._token = token
        self._timeout = timeout

    def close(self) -> None:
        """No persistent resources to release; each call opens its own RelayClient."""
        return None

    def __call__(
        self,
        action: AskUserAction,
        conversation: "LocalConversation | None" = None,
    ) -> AskUserObservation:
        question_id = str(uuid.uuid4())
        try:
            answer = asyncio.run(self._ask(action.question, question_id))
        except Exception as exc:
            return AskUserObservation.from_text(text=f"Error: {exc}", is_error=True)
        return AskUserObservation.from_text(text=answer, answer=answer)

    async def _ask(self, question: str, question_id: str) -> str:
        async with make_client() as client:
            reply = await client.ask(self._token, question, question_id, timeout=self._timeout)
            return reply.text


class AskUserTool(ToolDefinition[AskUserAction, AskUserObservation]):
    """Typed ask-the-user tool for prompt agents."""

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Sequence["AskUserTool"]:
        label: str | None = kwargs.get("label")
        token: str | None = kwargs.get("token")
        resolved_token = resolve(label=label or DEFAULT_LABEL, token=token)
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=AskUserAction,
                observation_type=AskUserObservation,
                annotations=ToolAnnotations(
                    title="ask_user",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=AskUserExecutor(token=resolved_token, timeout=ASK_USER_TIMEOUT_SECONDS),
            )
        ]


register_tool(AskUserTool.name, AskUserTool)
