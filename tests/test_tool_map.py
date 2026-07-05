"""Tests for goblin.utils.tool_map — blueprint tool name resolution."""

from __future__ import annotations

from openhands.sdk import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.glob import GlobTool
from openhands.tools.grep import GrepTool
from openhands.tools.terminal import TerminalTool

from goblin.utils.tool_map import TOOL_NAME_MAP, build_tool_filter_regex, resolve_tools


class TestToolNameMap:
    def test_bash_maps_to_terminal(self):
        assert TOOL_NAME_MAP["Bash"].name == TerminalTool.name

    def test_read_maps_to_file_editor(self):
        assert TOOL_NAME_MAP["Read"].name == FileEditorTool.name

    def test_write_maps_to_file_editor(self):
        assert TOOL_NAME_MAP["Write"].name == FileEditorTool.name

    def test_edit_maps_to_file_editor(self):
        assert TOOL_NAME_MAP["Edit"].name == FileEditorTool.name

    def test_glob_maps_to_glob(self):
        assert TOOL_NAME_MAP["Glob"].name == GlobTool.name

    def test_grep_maps_to_grep(self):
        assert TOOL_NAME_MAP["Grep"].name == GrepTool.name


class TestResolveTools:
    def test_resolves_bash(self):
        result = resolve_tools(["Bash"])
        assert len(result) == 1
        assert result[0].name == TerminalTool.name

    def test_deduplicates_file_editor(self):
        result = resolve_tools(["Read", "Write", "Edit"])
        names = [t.name for t in result]
        assert names.count(FileEditorTool.name) == 1

    def test_deduplicates_terminal(self):
        result = resolve_tools(["Bash", "terminal"])
        names = [t.name for t in result]
        assert names.count(TerminalTool.name) == 1

    def test_unknown_name_passes_through(self):
        result = resolve_tools(["MyMcpTool"])
        assert len(result) == 1
        assert result[0].name == "MyMcpTool"

    def test_mixed_known_and_unknown(self):
        result = resolve_tools(["Bash", "SomeMcpTool"])
        names = [t.name for t in result]
        assert TerminalTool.name in names
        assert "SomeMcpTool" in names

    def test_empty_list_returns_empty(self):
        assert resolve_tools([]) == []

    def test_preserves_order_known_first(self):
        result = resolve_tools(["Glob", "Grep"])
        assert result[0].name == GlobTool.name
        assert result[1].name == GrepTool.name

    def test_returns_tool_instances(self):
        result = resolve_tools(["Bash"])
        assert all(isinstance(t, Tool) for t in result)

    def test_mcp_patterns_are_skipped(self):
        result = resolve_tools(["mcp__github__list_issues", "mcp__github__*"])
        assert result == []

    def test_wildcard_patterns_are_skipped(self):
        result = resolve_tools(["Bash*", "some__*"])
        assert result == []

    def test_mixed_builtin_and_mcp_skips_mcp(self):
        result = resolve_tools(["Bash", "mcp__github__list_issues"])
        assert len(result) == 1
        assert result[0].name == TerminalTool.name


class TestBuildToolFilterRegex:
    def test_returns_none_when_no_mcp_patterns(self):
        assert build_tool_filter_regex(["Bash", "Read"]) is None

    def test_returns_none_for_empty_list(self):
        assert build_tool_filter_regex([]) is None

    def test_specific_mcp_tool(self):
        regex = build_tool_filter_regex(["mcp__github__list_issues"])
        assert regex is not None
        import re

        pattern = re.compile(regex)
        assert pattern.match("list_issues")
        assert not pattern.match("create_issue")

    def test_wildcard_mcp_pattern(self):
        regex = build_tool_filter_regex(["mcp__github__*"])
        assert regex is not None
        import re

        pattern = re.compile(regex)
        assert pattern.match("list_issues")
        assert pattern.match("create_pull_request")
        assert pattern.match("anything")

    def test_mixed_builtin_and_mcp(self):
        regex = build_tool_filter_regex(["Bash", "mcp__github__list_issues"])
        assert regex is not None
        import re

        pattern = re.compile(regex)
        assert pattern.match(TerminalTool.name)
        assert pattern.match("list_issues")
        assert not pattern.match("create_issue")
        assert not pattern.match(FileEditorTool.name)

    def test_multiple_mcp_tools(self):
        regex = build_tool_filter_regex(["mcp__github__list_issues", "mcp__github__create_issue"])
        assert regex is not None
        import re

        pattern = re.compile(regex)
        assert pattern.match("list_issues")
        assert pattern.match("create_issue")
        assert not pattern.match("delete_repository")

    def test_multiple_mcp_servers(self):
        regex = build_tool_filter_regex(["mcp__github__list_issues", "mcp__linear__create_issue"])
        assert regex is not None
        import re

        pattern = re.compile(regex)
        assert pattern.match("list_issues")
        assert pattern.match("create_issue")
