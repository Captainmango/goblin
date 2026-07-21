"""Tests for the shared run_conversation() helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from goblin.conversation import run_conversation


class _FakeRemoteWorkspace:
    pass


class TestRunConversationLocal:
    @pytest.mark.asyncio
    async def test_uses_local_conversation_and_arun(self):
        conversation = MagicMock()
        conversation.arun = AsyncMock()

        with patch("goblin.conversation.LocalConversation", return_value=conversation) as mock_conv_cls:
            result = await run_conversation(agent=MagicMock(), workspace=MagicMock(), prompt="hi", max_iterations=500)

        mock_conv_cls.assert_called_once()
        conversation.arun.assert_awaited_once()
        conversation.run.assert_not_called()
        assert result is conversation


class TestRunConversationRemote:
    @pytest.mark.asyncio
    async def test_uses_remote_conversation_and_to_thread(self):
        conversation = MagicMock()
        conversation.run = MagicMock()
        workspace = _FakeRemoteWorkspace()

        with (
            patch("goblin.conversation.RemoteWorkspace", _FakeRemoteWorkspace),
            patch("goblin.conversation.RemoteConversation", return_value=conversation) as mock_conv_cls,
            patch("goblin.conversation.asyncio.to_thread") as mock_to_thread,
        ):
            mock_to_thread.return_value = AsyncMock()
            result = await run_conversation(agent=MagicMock(), workspace=workspace, prompt="hi", max_iterations=500)

        mock_conv_cls.assert_called_once()
        mock_to_thread.assert_awaited_once()
        assert mock_to_thread.call_args.args[0] is conversation.run
        assert result is conversation


class TestRunConversationSecrets:
    @pytest.mark.asyncio
    async def test_secrets_passed_through_when_given(self):
        conversation = MagicMock()
        conversation.arun = AsyncMock()

        with patch("goblin.conversation.LocalConversation", return_value=conversation) as mock_conv_cls:
            await run_conversation(
                agent=MagicMock(),
                workspace=MagicMock(),
                prompt="hi",
                max_iterations=500,
                secrets={"SWARMDB_PATH": "/tmp/db"},
            )

        assert mock_conv_cls.call_args.kwargs["secrets"] == {"SWARMDB_PATH": "/tmp/db"}

    @pytest.mark.asyncio
    async def test_secrets_none_by_default(self):
        conversation = MagicMock()
        conversation.arun = AsyncMock()

        with patch("goblin.conversation.LocalConversation", return_value=conversation) as mock_conv_cls:
            await run_conversation(agent=MagicMock(), workspace=MagicMock(), prompt="hi", max_iterations=500)

        assert mock_conv_cls.call_args.kwargs["secrets"] is None


class TestRunConversationSendMessage:
    @pytest.mark.asyncio
    async def test_sends_prompt_before_running(self):
        conversation = MagicMock()
        conversation.arun = AsyncMock()

        with patch("goblin.conversation.LocalConversation", return_value=conversation):
            await run_conversation(agent=MagicMock(), workspace=MagicMock(), prompt="do the thing", max_iterations=500)

        conversation.send_message.assert_called_once_with("do the thing")
