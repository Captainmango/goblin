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

## Provider registry and pinned versions

When `type: acp`, goblin uses the OpenHands SDK's provider registry to resolve
the harness subprocess command. The pinned defaults are:

| `server` | Default command | Auth env var |
|---|---|---|
| `claude-code` | `npx -y @agentclientprotocol/claude-agent-acp@0.44.0` | `ANTHROPIC_API_KEY` |
| `codex` | `npx -y @zed-industries/codex-acp@0.16.0` | `OPENAI_API_KEY` |
| `gemini-cli` | `npx -y @google/gemini-cli@0.46.0 --acp` | `GEMINI_API_KEY` |
| `custom` | value of `command` | depends on command |

Use `command` to override the pinned default with a pre-installed binary or a
different package version.

## Provider credentials

ACP harnesses read provider keys from the ambient environment:

- Claude Code: `ANTHROPIC_API_KEY`
- Codex: `OPENAI_API_KEY`
- Gemini CLI: `GEMINI_API_KEY`

You can also pass per-blueprint overrides via `harness.secrets`; values are masked in
events and logs. Reserved secret names for file-based credentials (e.g. Codex
`auth.json`, Gemini service-account JSON) are materialised to disk by the SDK with
restrictive permissions.

Goblin reads the ambient provider key listed above and injects it into the
conversation secrets channel. This keeps the key masked in events and logs while still
letting `harness.secrets` override it when you need a per-blueprint key.

For ACP phases that declare `output_model` or `output_schema`, `model` / `llm` (phase
or defaults) configure the goblin-side structured-output coercion LLM, not the
harness's model. The harness's model is `harness.model`.

## Structured-output repair

When an ACP phase declares `output_model` or `output_schema` and the harness response
does not parse or validate, goblin runs a short goblin-side LLM repair to extract the
required shape. By default this repair uses the phase/default `model` / `llm` config, and
if that config has no `api_key`, goblin falls back to the ACP provider's API key (so a
Codex harness will repair via OpenAI unless you explicitly set a different coercion LLM).
Set an explicit `model` or `llm.api_key` on the phase or blueprint `defaults` to use a
different provider for repair.

## Session semantics

Each ACP phase execution gets one fresh harness subprocess and one fresh ACP session
keyed by `working_dir`:

- The harness owns session state, tool execution, and any native project config
  (`CLAUDE.md` hierarchy, `.claude/settings.json`, `.mcp.json`).
- `load_project` is unnecessary for pure-ACP runs; it is still useful for setting
  `project_path` as the `working_dir` fallback or for mixed blueprints that also run
  OpenHands phases.
- `foreach` creates one independent session per iteration. `max_iterations` still
  limits the total number of executions, not LLM turns.

## Permission posture

ACP providers default to unattended modes such as `bypassPermissions` or `full-access`,
which auto-approve tool calls. Pin `session_mode` to a stricter value if you need
different behaviour.

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

## Field compatibility under ACP

| Phase field | Under ACP | Disposition |
|---|---|---|
| `prompt`, `condition`, `foreach`, `depends_on`, `history`, `on_failure`, `vars` | Works unchanged | Keep |
| `mcp_servers` | Forwarded to `session/new` | Keep |
| `model` / `llm` | Coercion LLM only | Keep |
| `max_cost`, `max_tokens` | Read from ACP usage accounting | Keep |
| `max_iterations` | One prompt turn per execution; still bounds executions (e.g. `foreach`, retries) | Validation warning |
| `allowed_tools` | Harness owns tools | Validation error |
| `hooks` | OpenHands hooks don't fire | Validation error |
| `worktree` | OpenHands tool | Validation error |
| `output_model` / `output_schema` | Coerced after the turn | Keep |
| `workspace: {type: remote}` | ACP needs local subprocess | Validation error |
| `server: custom` without `command` | SDK raises at runtime | Validation error |
| `command` with non-`custom` `server` | Overrides pinned command | Validation warning |

## Node / npx prerequisite

The default ACP commands use `npx`, which requires Node.js and npm on `PATH`. The first
download of a pinned package can be slow; use `command` to point at a pre-installed
binary to avoid `npx` latency.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Failed to build ACP agent" | Missing CLI or `server: custom` without `command` | Install Node/npm or set `command`. |
| "ACP harness failed" / timeout | Harness wedged or `prompt_timeout` exceeded | Increase `prompt_timeout` or check provider status. |
| MCP server not visible to harness | Transport not advertised or server crashed | Use stdio servers; check harness logs. |
| `max_cost` / `max_tokens` silently ignored | Provider emits no usage updates | Treat cost limits as best-effort for that provider; rely on `prompt_timeout`. |
| Concurrent phases corrupt shared config | Race on `~/.claude` / `CODEX_HOME` | Set `isolate_data_dir: true`. |
