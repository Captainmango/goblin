from __future__ import annotations

import asyncio

from openhands.sdk import LocalConversation, RemoteConversation, RemoteWorkspace
from openhands.sdk.hooks import HookConfig


async def run_conversation(
    agent,
    workspace,
    prompt: str,
    *,
    hook_config: HookConfig | None = None,
    max_iterations: int,
    max_cost: float | None = None,
    secrets: dict | None = None,
) -> LocalConversation | RemoteConversation:
    """Build a conversation for *workspace*, send *prompt*, and run it to completion.

    Uses RemoteConversation (run in a thread, since RemoteConversation.run is
    synchronous) for a RemoteWorkspace, and LocalConversation.arun() for
    everything else. Returns the conversation so the caller can read
    conversation.state.events and pass the conversation to
    CircuitBreaker.absorb().
    """
    is_remote = isinstance(workspace, RemoteWorkspace)
    conversation_cls = RemoteConversation if is_remote else LocalConversation

    conversation = conversation_cls(
        agent=agent,
        workspace=workspace,
        hook_config=hook_config,
        max_iteration_per_run=max_iterations,
        max_budget_per_run=max_cost,
        secrets=secrets,
    )
    conversation.send_message(prompt)

    if is_remote:
        await asyncio.to_thread(conversation.run)
    else:
        await conversation.arun()

    return conversation
