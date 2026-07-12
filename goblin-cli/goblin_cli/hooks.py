from __future__ import annotations

from typing import Any

from openhands.sdk.hooks import HookDefinition

HOOK_REGISTRY: dict[str, Any] = {}


def hook(name: str, *, command: str, timeout: int = 60):
    """Register a named hook by shell command."""

    def decorator(fn):
        HOOK_REGISTRY[name] = HookDefinition(command=command, timeout=timeout)
        return fn

    return decorator


def register_hook(name: str, definition: HookDefinition) -> None:
    HOOK_REGISTRY[name] = definition
