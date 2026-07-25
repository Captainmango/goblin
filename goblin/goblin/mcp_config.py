from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _coerce_auth(auth: Any) -> Any:
    """Map legacy string auth values to the SDK's credential models.

    Goblin's schema accepts ``auth`` as a bearer token string or the literal
    ``"oauth"``.  The SDK's ``MCPServer.auth`` expects a discriminated
    credential union.
    """
    if auth is None or isinstance(auth, dict):
        return auth
    if isinstance(auth, str):
        from openhands.sdk.mcp.config import (
            MCPBearerAuthCredential,
            MCPOAuthAuthCredential,
        )

        if auth.lower() == "oauth":
            return MCPOAuthAuthCredential(strategy="oauth2")
        return MCPBearerAuthCredential(strategy="bearer", value=auth)
    return auth


def to_native_mcp_servers(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Convert Goblin's raw MCP config to the SDK's native ``dict[str, MCPServer]``.

    Goblin keeps blueprint MCP servers as plain nested dicts
    (``{"mcpServers": {"name": {"command": ..., "env": ...}}}``).  Newer
    OpenHands SDK versions expect ``MCPServer`` pydantic model instances
    instead.  This function performs that coercion at the SDK boundary.

    Mapping performed:
      - ``type`` is aliased to ``transport`` when ``transport`` is absent.
      - ``env``/``headers`` string values are coerced to ``SecretStr`` by
        pydantic.
      - ``auth`` string values are mapped to SDK credential models.
      - Extra fields not accepted by ``MCPServer`` are dropped with a warning.
    """
    from openhands.sdk.mcp.config import MCPServer

    servers = raw_config.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {}

    native: dict[str, Any] = {}
    allowed_fields = set(MCPServer.model_fields.keys())

    for name, server in servers.items():
        if not isinstance(server, dict):
            logger.warning(
                "Skipping non-dict MCP server config",
                extra={"event": "mcp_config.skip_non_dict", "server": name},
            )
            continue

        cleaned: dict[str, Any] = dict(server)

        # Goblin's schema allows `type` as an alternative to `transport`.
        if cleaned.get("transport") is None and cleaned.get("type") is not None:
            cleaned["transport"] = cleaned.pop("type")
        elif "type" in cleaned:
            cleaned.pop("type")

        # Map legacy string auth to credential models.
        if "auth" in cleaned:
            cleaned["auth"] = _coerce_auth(cleaned["auth"])

        # Drop fields the current SDK MCPServer model does not accept.
        extras = set(cleaned.keys()) - allowed_fields
        if extras:
            logger.warning(
                "Dropping unsupported MCP server fields for SDK compatibility",
                extra={
                    "event": "mcp_config.drop_extras",
                    "server": name,
                    "fields": sorted(extras),
                },
            )
            for field in extras:
                cleaned.pop(field)

        try:
            native[name] = MCPServer(**cleaned)
        except Exception as exc:
            logger.warning(
                "Failed to convert MCP server config; server will be omitted",
                extra={
                    "event": "mcp_config.convert_failed",
                    "server": name,
                    "error": str(exc),
                },
            )

    return native
