# MCP Servers

Goblin supports [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers, allowing agents to use external tools via stdio subprocesses or remote HTTP/SSE transports.

## Configuration

MCP servers are configured in the `mcp_servers` block:

```yaml
phases:
  - id: analyse
    prompt: "List open issues in the repo"
    allowed_tools: [mcp__github__list_issues, mcp__github__get_issue]
    mcp_servers:
      github:
        command: npx
        args: [-y, "@modelcontextprotocol/server-github"]
        env:
          GITHUB_TOKEN: $GITHUB_TOKEN
```

## Tool Naming in `allowed_tools`

MCP tools are referenced using a triple-underscore pattern:

```
mcp__<server_name>__<tool_name>
```

Examples:

```yaml
allowed_tools:
  - mcp__github__list_issues
  - mcp__github__get_issue
  - mcp__github__create_issue
```

Use a wildcard to allow all tools from a server:

```yaml
allowed_tools:
  - mcp__github__*
```

## Transports

### Stdio (Subprocess)

```yaml
mcp_servers:
  github:
    command: npx
    args: [-y, "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: $GITHUB_TOKEN
    cwd: /tmp
```

### Remote (HTTP / SSE)

```yaml
mcp_servers:
  my_server:
    transport: sse
    url: https://example.com/mcp/sse
    headers:
      Authorization: Bearer $API_TOKEN
```

Valid transports: `http`, `streamable-http`, `sse`.

## Environment Variables

String values in `mcp_servers` support `$VAR` environment variable references and `{variable}` context interpolation. The full parent process environment is injected into stdio subprocesses so MCP servers see the same tokens as Goblin.

## Precedence

Phase-level `mcp_servers` are merged with blueprint `defaults.mcp_servers`. Phase-level keys override defaults.

## Per-Agent MCP Servers (Swarm)

In a swarm, individual agents can declare their own `mcp_servers`:

```yaml
swarm:
  agents:
    - name: backend
      prompt: "..."
      allowed_tools: [Bash, mcp__db__query]
      mcp_servers:
        db:
          command: uvx
          args: [mcp-server-sqlite, --db-path, /tmp/app.db]
```
