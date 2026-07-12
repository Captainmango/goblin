from openhands.sdk import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.glob import GlobTool
from openhands.tools.grep import GrepTool
from openhands.tools.terminal import TerminalTool

from goblin.utils.swarm_agent_tool import SwarmAgentTool
from goblin.utils.swarmdb_tool import SwarmTasksTool
from goblin.utils.worktree_tool import WorktreeTool

TOOL_NAME_MAP: dict[str, Tool] = {
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
}


def resolve_tools(names: list[str], tool_params: dict[str, dict] | None = None) -> list[Tool]:
    """Map blueprint tool names to OpenHands Tool specs.

    MCP tool patterns (mcp__* or wildcards) are skipped — MCP tools are
    wired in via mcp_config, not the tools list.
    Deduplicates by resolved tool name so Read+Write+Edit all collapse to one FileEditorTool.

    `tool_params` optionally supplies per-blueprint-name `.create()` params
    (e.g. `{"SwarmTasks": {"db": swarm_db_instance}}`) for tools that need
    caller-provided state that can't live in the static TOOL_NAME_MAP.
    """
    tool_params = tool_params or {}
    seen: set[str] = set()
    result: list[Tool] = []
    for name in names:
        if "*" in name or name.startswith("mcp__"):
            continue
        tool = TOOL_NAME_MAP.get(name, Tool(name=name))
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


def build_tool_filter_regex(names: list[str]) -> str | None:
    """Build a filter_tools_regex for the Agent from the allowed_tools list.

    Returns None when no MCP patterns are present — in that case built-in
    tool restriction is already handled by resolve_tools and no regex is needed.

    When MCP patterns are present the returned regex matches:
      - the resolved OpenHands names of any listed built-in tools, AND
      - the raw MCP tool names derived from mcp__server__tool entries.

    Built-in tools from Agent.include_default_tools (FinishTool, ThinkTool)
    are excluded from the filter: the SDK adds them after the regex is applied.
    """
    mcp_patterns = [n for n in names if n.startswith("mcp__")]
    if not mcp_patterns:
        return None

    builtin_names = sorted(
        {TOOL_NAME_MAP.get(n, Tool(name=n)).name for n in names if not n.startswith("mcp__") and "*" not in n}
    )
    mcp_fragments = [_mcp_pattern_to_regex(p) for p in mcp_patterns]

    all_fragments = builtin_names + mcp_fragments
    return "^(" + "|".join(all_fragments) + ")$"
