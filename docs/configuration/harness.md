# Harness configuration

The `harness` block selects the execution backend for a prompt phase. It can be
declared in `defaults` (inherited by all phases) or on an individual phase.

```yaml
version: v1
blueprint:
  name: example
  defaults:
    harness:
      type: acp
      server: claude-code
  phases:
    - id: fix_api
      prompt: "Fix the null-pointer in the payments endpoint."
      harness:
        working_dir: "{api_dir}"
```

## Backend types

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `openhands` \| `acp` | `openhands` | Execution backend. `openhands` runs goblin's native OpenHands agent loop; `acp` delegates to a local ACP-compatible harness subprocess. |
| `server` | `claude-code` \| `codex` \| `gemini-cli` \| `custom` | `claude-code` | ACP provider. Selects the SDK's pinned default command; `custom` requires `command`. |
| `command` | `list[str]` | `[]` | Explicit ACP server launch command. Required when `server: custom`. |
| `args` | `list[str]` | `[]` | Extra args appended to the command. Supports `{variable}` interpolation. |
| `model` | `str` | provider default | Model identity inside the harness (e.g. `sonnet`). Applied via the ACP protocol. |
| `session_mode` | `str` | provider default | ACP session mode ID (e.g. `bypassPermissions`). |
| `prompt_timeout` | `number` | `1800` | Seconds of turn inactivity before the prompt is aborted. Resets on every session update. |
| `working_dir` | `str` | workspace → project → cwd | Working directory that becomes the ACP session `cwd`. Supports `{variable}` interpolation. |
| `isolate_data_dir` | `bool` | `false` | Give the harness a per-conversation config/data root so concurrent phases don't race on shared CLI state. |
| `secrets` | `map[str, str]` | `{}` | Env vars delivered to the harness subprocess via the conversation secrets channel (masked in events/logs; overrides ambient env). Values support `${VAR}` references. |

## MCP forwarding

Blueprint `mcp_servers` (defaults + phase) are merged, resolved, and forwarded to the
ACP harness via `ACPAgentSettings.mcp_config`. The SDK translates them into ACP
`session/new` servers.

Important ownership split: under ACP, MCP servers run as children of the harness
subprocess in the session `cwd`, not of the goblin process. Relative `cwd`/`args` in a
server config resolve against the harness's environment, so prefer absolute paths.

If a remote (http/sse) server is silently dropped, the harness did not advertise that
transport during `initialize`. Check the SDK logs for the drop and use stdio servers, or
ensure the harness supports the transport.

## Multi-repo execution

ACP sessions are scoped to a `cwd` and owned by the harness subprocess. A blueprint can
fan out across repositories by giving each phase its own `harness.working_dir`:

```yaml
blueprint:
  vars:
    api_dir: /home/dev/repos/api-service
    ui_dir: /home/dev/repos/web-ui
  phases:
    - id: fix_api
      prompt: "Fix the null-pointer in the payments endpoint."
      harness:
        working_dir: "{api_dir}"
    - id: fix_ui
      prompt: "Update the checkout form for the new error shape."
      depends_on: [fix_api]
      harness:
        working_dir: "{ui_dir}"
```

Phases in the same dependency wave run concurrently with independent harness sessions.
Use `foreach: repos.txt` and `harness.working_dir: "{item}"` to roll a change out across
multiple repos.

## Concurrency and `isolate_data_dir`

Parallel ACP phases using the same provider can race on the CLI's global config dir
(`~/.claude`, `CODEX_HOME`).

| Scenario | Recommendation |
|---|---|
| Parallel ACP phases with the same provider | Set `isolate_data_dir: true` on the harness. |
| Sequential phases, or shared harness config/auth desired | Leave `isolate_data_dir: false` (default). |
| `gemini-cli` | Isolation relocates `HOME`, which has a wide blast radius on `git`/`npm` configs; use with care. |

## Workspace interaction

| Blueprint `workspace` | Phase `harness.type` | Result |
|---|---|---|
| absent / local | `openhands` | Native OpenHands agent loop (today's behavior). |
| absent / local | `acp` | ACP subprocess; `cwd` falls back through harness → workspace → project → `.` |
| `remote` | `openhands` | Remote OpenHands agent server (today's behavior). |
| `remote` | `acp` | Validation error; ACP requires a local subprocess. |

## Provider credentials

ACP harnesses read provider keys from the ambient environment:

- Claude Code: `ANTHROPIC_API_KEY`
- Codex: `OPENAI_API_KEY`
- Gemini CLI: `GEMINI_API_KEY`

You can also pass per-blueprint overrides via `harness.secrets`; values are masked in
events and logs.
