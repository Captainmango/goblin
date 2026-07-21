from __future__ import annotations

from typing import Any

from openhands.sdk.hooks import HookDefinition

HOOK_REGISTRY: dict[str, Any] = {}


def register_hook(name: str, definition: HookDefinition) -> None:
    HOOK_REGISTRY[name] = definition
