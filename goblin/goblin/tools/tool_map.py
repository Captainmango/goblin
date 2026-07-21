from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.sdk import Tool

_TOOL_NAME_MAP: dict[str, Tool] | None = None


def _get_tool_name_map() -> dict[str, Tool]:
    """Lazily build the tool name map on first use."""
    global _TOOL_NAME_MAP
    if _TOOL_NAME_MAP is not None:
        return _TOOL_NAME_MAP

    from openhands.sdk import Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.glob import GlobTool
    from openhands.tools.grep import GrepTool
    from openhands.tools.terminal import TerminalTool

    from goblin.tools.ask_user_tool import AskUserTool
    from goblin.tools.swarm_agent_tool import SwarmAgentTool
    from goblin.tools.swarmdb_tool import SwarmTasksTool
    from goblin.tools.worktree_tool import WorktreeTool

    _TOOL_NAME_MAP = {
        "Bash": Tool(name=TerminalTool.name),
        "terminal": Tool(name=TerminalTool.name),
        "Read": Tool(name=FileEditorTool.name),
        "Write": Tool(name=FileEditorTool.name),
        "Edit": Tool(name=FileEditorTool.name),
        "MultiEdit": Tool(name=FileEditorTool.name),
        "Glob": Tool(name=GlobTool.name),
        "Grep": Tool(name=GrepTool.name),
        "SwarmTasks": Tool(name=SwarmTasksTool.name),
        "SwarmAgent": Tool(name=SwarmAgentTool.name),
        "Worktree": Tool(name=WorktreeTool.name),
        "AskUser": Tool(name=AskUserTool.name),
    }
    return _TOOL_NAME_MAP


def resolve_tools(names: list[str], tool_params: dict[str, dict] | None = None) -> list[Tool]:
    """Map blueprint tool names to OpenHands Tool specs.

    MCP tool patterns (mcp__* or wildcards) are skipped — MCP tools are
    wired in via mcp_config, not the tools list.
    Deduplicates by resolved tool name so Read+Write+Edit all collapse to one FileEditorTool.

    `tool_params` optionally supplies per-blueprint-name `.create()` params
    (e.g. `{"SwarmTasks": {"db": swarm_db_instance}}`) for tools that need
    caller-provided state that can't live in the static TOOL_NAME_MAP.
    """
    from openhands.sdk import Tool

    tool_params = tool_params or {}
    seen: set[str] = set()
    result: list[Tool] = []
    tool_map = _get_tool_name_map()
    for name in names:
        if "*" in name or name.startswith("mcp__"):
            continue
        tool = tool_map.get(name, Tool(name=name))
        if name in tool_params:
            tool = Tool(name=tool.name, params=tool_params[name])
        if tool.name not in seen:
            result.append(tool)
            seen.add(tool.name)
    return result


def _mcp_pattern_to_regex(pattern: str) -> str:
    """Convert an mcp__server__tool pattern to a raw tool-name regex fragment.

    mcp__github__list_issues  ->  list_issues
    mcp__github__*            ->  .*
    """
    parts = pattern.split("__", 2)
    raw = parts[2] if len(parts) >= 3 else "*"
    return raw.replace("*", ".*")


def build_tool_filter_regex(
    names: list[str],
    extra_builtin_names: list[str] | None = None,
) -> str | None:
    """Build a filter_tools_regex for the Agent from the allowed_tools list.

    Returns None when no MCP patterns are present — in that case built-in
    tool restriction is already handled by resolve_tools and no regex is needed.

    When MCP patterns are present the returned regex matches:
      - the resolved OpenHands names of any listed built-in tools, AND
      - any additional ``extra_builtin_names`` (e.g. the typed Finish tool),
        so tools swapped in after ``resolve_tools`` are not filtered out, AND
      - the raw MCP tool names derived from mcp__server__tool entries.

    Built-in tools from Agent.include_default_tools (FinishTool, ThinkTool)
    are excluded from the filter unless they are passed in ``extra_builtin_names``.
    """
    from openhands.sdk import Tool

    mcp_patterns = [n for n in names if n.startswith("mcp__")]
    if not mcp_patterns and not extra_builtin_names:
        return None

    tool_map = _get_tool_name_map()
    builtin_names = sorted(
        {tool_map.get(n, Tool(name=n)).name for n in names if not n.startswith("mcp__") and "*" not in n}
    )
    if extra_builtin_names:
        builtin_names = sorted(set(builtin_names) | set(extra_builtin_names))
    mcp_fragments = [_mcp_pattern_to_regex(p) for p in mcp_patterns]

    all_fragments = builtin_names + mcp_fragments
    return "^(" + "|".join(all_fragments) + ")$"
