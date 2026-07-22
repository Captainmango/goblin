"""Tests for the typed ask_user tool that lets an agent ask the human a question.

Exercises the ToolDefinition/ToolExecutor pair directly with a fake
RelayClient standing in for the notifications-relay HTTP round trip -- no
OpenHands SDK conversation/agent machinery is started, per
.claude/rules/testing.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from goblin.tools.ask_user_tool import (
    ASK_USER_TIMEOUT_SECONDS,
    AskUserAction,
    AskUserExecutor,
    AskUserObservation,
    AskUserTool,
)
from notifications_relay.exceptions import AskTimeoutError


class _FakeReply:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeRelayClient:
    def __init__(self, reply_text: str | None = None, error: Exception | None = None) -> None:
        self._reply_text = reply_text
        self._error = error
        self.ask = AsyncMock(side_effect=self._ask)

    async def _ask(self, token: str, text: str, question_id: str, timeout: float) -> _FakeReply:
        if self._error is not None:
            raise self._error
        return _FakeReply(self._reply_text or "")

    async def __aenter__(self) -> _FakeRelayClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class TestAskUserExecutorCall:
    def test_returns_reply_text(self):
        fake_client = _FakeRelayClient(reply_text="yes, go ahead")
        with patch("goblin.tools.ask_user_tool.make_client", return_value=fake_client):
            executor = AskUserExecutor(token="tok123")
            obs = executor(AskUserAction(question="Should I proceed?"))

        assert isinstance(obs, AskUserObservation)
        assert obs.is_error is False
        assert obs.answer == "yes, go ahead"
        assert obs.text == "yes, go ahead"

    def test_sends_the_token_and_question(self):
        fake_client = _FakeRelayClient(reply_text="ok")
        with patch("goblin.tools.ask_user_tool.make_client", return_value=fake_client):
            executor = AskUserExecutor(token="tok123")
            executor(AskUserAction(question="Continue?"))

        fake_client.ask.assert_awaited_once()
        call = fake_client.ask.call_args
        assert call.args[0] == "tok123"
        assert call.args[1] == "Continue?"

    def test_passes_configured_timeout(self):
        fake_client = _FakeRelayClient(reply_text="ok")
        with patch("goblin.tools.ask_user_tool.make_client", return_value=fake_client):
            executor = AskUserExecutor(token="tok123", timeout=42.0)
            executor(AskUserAction(question="Continue?"))

        assert fake_client.ask.call_args.kwargs["timeout"] == 42.0

    def test_default_timeout_is_fifteen_minutes(self):
        assert ASK_USER_TIMEOUT_SECONDS == 15 * 60

    def test_default_executor_uses_fifteen_minute_timeout(self):
        fake_client = _FakeRelayClient(reply_text="ok")
        with patch("goblin.tools.ask_user_tool.make_client", return_value=fake_client):
            executor = AskUserExecutor(token="tok123")
            executor(AskUserAction(question="Continue?"))

        assert fake_client.ask.call_args.kwargs["timeout"] == ASK_USER_TIMEOUT_SECONDS

    def test_timeout_error_returns_error_observation(self):
        fake_client = _FakeRelayClient(error=AskTimeoutError("no reply received"))
        with patch("goblin.tools.ask_user_tool.make_client", return_value=fake_client):
            executor = AskUserExecutor(token="tok123")
            obs = executor(AskUserAction(question="Are you there?"))

        assert obs.is_error is True
        assert "no reply received" in obs.text
        assert obs.answer is None


class TestAskUserToolCreate:
    def test_create_with_explicit_token_skips_resolution(self):
        tools = AskUserTool.create(token="explicit-token")

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "ask_user"
        assert isinstance(tool.executor, AskUserExecutor)
        assert tool.executor._token == "explicit-token"
        assert tool.executor._timeout == ASK_USER_TIMEOUT_SECONDS

    def test_create_resolves_default_label_when_unspecified(self):
        captured: dict[str, str | None] = {}

        def _fake_resolve(label: str | None = None, token: str | None = None) -> str:
            captured["label"] = label
            captured["token"] = token
            return "resolved-token"

        with patch("goblin.tools.ask_user_tool.resolve", _fake_resolve):
            tools = AskUserTool.create()

        assert captured["label"] == "default"
        assert tools[0].executor._token == "resolved-token"

    def test_create_passes_through_custom_label(self):
        captured: dict[str, str | None] = {}

        def _fake_resolve(label: str | None = None, token: str | None = None) -> str:
            captured["label"] = label
            return "resolved-token"

        with patch("goblin.tools.ask_user_tool.resolve", _fake_resolve):
            AskUserTool.create(label="ops-oncall")

        assert captured["label"] == "ops-oncall"
