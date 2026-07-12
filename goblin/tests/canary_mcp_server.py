#!/usr/bin/env python3
"""Minimal MCP server that exposes its environment variables via a tool.

Used to verify that env vars specified in blueprint MCP config actually
reach the stdio subprocess.  When launched, the server reads CANARY_TOKEN
from its environment and exposes it through the `get_env` tool.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("canary")

# Capture the env var at import time so we can verify it was injected.
_CANARY_TOKEN = os.environ.get("CANARY_TOKEN", "<NOT_SET>")


@mcp.tool()
def get_env(key: str) -> str:
    """Return the value of an environment variable."""
    return os.environ.get(key, "<NOT_SET>")


@mcp.tool()
def get_canary() -> str:
    """Return the CANARY_TOKEN value captured at server startup."""
    return _CANARY_TOKEN


if __name__ == "__main__":
    mcp.run()
