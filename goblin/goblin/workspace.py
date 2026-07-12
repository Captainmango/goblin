from __future__ import annotations

import logging
from typing import Any

from openhands.sdk import LocalWorkspace, RemoteWorkspace

logger = logging.getLogger(__name__)


def build_workspace(config: dict | None, project_path: str | None) -> LocalWorkspace | RemoteWorkspace:
    """Build an OpenHands workspace from a resolved config dict.

    Args:
        config: Resolved workspace config (may be None).
        project_path: Project path from load_project phase.

    Returns:
        LocalWorkspace or RemoteWorkspace instance.
    """
    if not config:
        return LocalWorkspace(working_dir=project_path or ".")

    ws_type = config.get("type", "local")
    working_dir = config.get("working_dir", project_path or ".")

    if ws_type == "remote":
        host = config["host"]
        kwargs: dict[str, Any] = {
            "host": host,
            "working_dir": working_dir,
        }
        if config.get("api_key"):
            kwargs["api_key"] = config["api_key"]
        if config.get("read_timeout") is not None:
            kwargs["read_timeout"] = config["read_timeout"]
        if config.get("max_connections") is not None:
            kwargs["max_connections"] = config["max_connections"]
        logger.info(
            "Using remote workspace",
            extra={"event": "workspace.remote", "host": host, "working_dir": working_dir},
        )
        return RemoteWorkspace(**kwargs)

    return LocalWorkspace(working_dir=working_dir)
