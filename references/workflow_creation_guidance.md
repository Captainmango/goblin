# Workflow Creation Guidance

Complete reference for creating GOBLIN blueprints — YAML workflow files validated against `spec/v1.json`.

---

## Blueprint Structure

Every blueprint has exactly two required top-level keys:

```yaml
# yaml-language-server: $schema=../spec/v1.json
version: v1
workflow:
  name: my_workflow
  steps: [...]
```

- **`version`** (required, string) — Currently only `"v1"` is supported.
- **`workflow`** (required, object) — Contains the entire workflow definition.

No other top-level keys are allowed.

### The `workflow` Object

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | yes | string | Unique identifier for the workflow (snake_case) |
| `steps` | yes | array | Ordered list of steps. At least one required. |
| `vars` | no | object | Workflow-level variables (all values must be strings). Used for `{variable}` interpolation. |
| `defaults` | no | object | Default configuration inherited by all steps (`hooks` and `mcp_servers`). |

### The `defaults` Block

```yaml
defaults:
  hooks:
    pre_tool: [hook_name]
    post_tool: [hook_name]
  mcp_servers:
    github:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "$GITHUB_TOKEN"
```

Hook lists apply to all prompt steps **unless** a step defines its own `hooks` block, which replaces the defaults entirely (no merging). MCP servers defined in defaults are available to all steps; step-level `mcp_servers` merge with defaults (step wins on name collision).

### Schema Directive

Add this comment as the first line of every blueprint for IDE validation:

```yaml
# yaml-language-server: $schema=../spec/v1.json
```

### Strict Validation

The schema uses `additionalProperties: false` on every object — typos in field names cause validation failures, and future fields must be added to `spec/v1.json` before use.

### Minimal Valid Blueprint

```yaml
version: v1
workflow:
  name: minimal
  steps:
    - id: greet
      prompt: "Say hello"
```

---

## Step Types

Every step must have an `id` and exactly one of `prompt` or `load_project`. This is enforced by a `oneOf` constraint — a step with both or neither fails validation.

### Prompt Steps

Runs a Claude agent with the given instruction.

```yaml
- id: resolve_ticket
  prompt: "Fix the bug described in {ticket_summary}"
  allowed_tools: [Bash, Read, Write, Edit]
  hooks:
    post_tool: [run_tests]
  condition: "{{ steps.clone_repo.success }}"
  on_failure:
    action: retry
    notify: slack_alert
```

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | yes | string | Unique identifier, referenced by conditions and foreach |
| `prompt` | yes | string | Agent instruction. Supports `{variable}` interpolation. |
| `allowed_tools` | no | array of strings | Whitelist of tools the agent may call |
| `hooks` | no | object | Step-level hooks (`pre_tool`, `post_tool`). Replaces defaults entirely. |
| `condition` | no | string | Jinja2 expression — step runs only if truthy |
| `foreach` | no | string | Iteration control (see Foreach section) |
| `on_failure` | no | object | Failure behavior: `action` + optional `notify` |

**Allowed Tools:** Common tools: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`. MCP tools: `mcp__<server>__<tool>` (e.g. `mcp__jira__update_ticket`). Omitting `allowed_tools` passes an empty list. Scope tools to the step's intent — use `[Read, Glob, Grep]` for read-only steps.

**Response Format:** Every prompt step expects the agent to return JSON: `{"success": true, "message": ["description of what was done"]}`. If the agent returns malformed JSON, the runner retries parsing up to 3 times.

### Load Project Steps

Loads configuration from a project root and applies it to all subsequent prompt steps. Does not run an agent.

```yaml
- id: load_project
  load_project: "{clone_dir}"
```

What it does:
1. **Settings** — Reads `{path}/.claude/settings.json`, passes to SDK. Extracts shell command hooks as `project_pre_tool` / `project_post_tool`.
2. **References** — Reads `{path}/CLAUDE.md` and `{path}/.claude/CLAUDE.md`, appends to system prompt.
3. **Agents** — Scans `{path}/.claude/agents/*.md` — each becomes an `AgentDefinition`.
4. **Working directory** — Sets `cwd` to the project path for subsequent steps.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | yes | string | Unique identifier |
| `load_project` | yes | string | Path to project root. Supports `{variable}` interpolation. |
| `condition` | no | string | Jinja2 expression — step runs only if truthy |
| `foreach` | no | string | Iteration control |
| `on_failure` | no | object | Failure behavior |

Always returns `{"success": true, "status": "loaded", "path": "..."}`.

### Step Ordering

- Steps execute in array order — no parallel execution.
- `load_project` must appear before prompt steps that need the loaded configuration.
- `depends_on` is defined in the schema but not enforced by the runner — rely on array order.
- Each step `id` must be unique.

---

## Variables, Interpolation, and Flow Control

### Blueprint Vars

Define workflow-level variables in `vars`. All values must be strings.

```yaml
vars:
  repo: https://github.com/org/service.git
  branch: main
  deploy_env: staging
```

### Runtime Context

Pass context at runtime via CLI:

```bash
uv run main.py workflows/deploy.yaml --context '{"repo": "my-service", "ticket_id": "PROJ-456"}'
```

### Merge Order

Blueprint vars and runtime context merge before execution. **Runtime context wins:**

```
final_context = {**blueprint_vars, **caller_context}
```

### `{variable}` Interpolation

Prompts and `load_project` paths support Python `.format()` interpolation:

| Field | Interpolated | Syntax |
|-------|-------------|--------|
| `prompt` | yes | `{variable}` |
| `load_project` | yes | `{variable}` |
| `condition` | no | `{{ jinja2 }}` |
| `foreach` | no | `{{ jinja2 }}` or integer or file path |
| `id`, `allowed_tools`, hook names | no | Literal strings |

**Escaping:** Literal curly braces must be doubled: `{{\"key\": \"value\"}}`. A missing variable raises `KeyError`.

### Conditions

The `condition` field uses Jinja2 expressions evaluated against prior step results. Falsy condition = step skipped.

```yaml
condition: "{{ steps.run_tests.success }}"
```

Available in expressions:
- `steps.<id>.success` — boolean from StepResult
- `steps.<id>.message` — list of message strings

**Built-in Filters:**
- `contains(text)` — true if any message contains the substring: `{{ steps.check.message | contains('all tests passed') }}`
- `json_value(dot.path)` — extract value from JSON messages: `{{ steps.api_call.message | json_value('data.status') == 'ok' }}`

**Truthy/Falsy:** `"false"`, `"none"`, `"0"`, and `""` are falsy. Everything else is truthy. The `{{ }}` wrapper is optional.

### Foreach

Controls iteration on a step. Accepts three forms:

**Integer** — Runs the step N times. `{index}` (0-based) available in prompt:
```yaml
foreach: "3"
```

**Jinja2 expression** — Must resolve to an integer. `{index}` available:
```yaml
foreach: "{{ steps.fetch_data.output.count }}"
```

**File path** — Runs once per non-empty line. Both `{index}` and `{item}` available:
```yaml
foreach: "tickets.txt"
```

Paths support interpolation (`foreach: "{output_dir}/urls.txt"`). Relative paths resolve from loaded project root.

**Results:** When using `foreach`, the result is `{"iterations": [...], "success": true}`. `success` is true only if every iteration succeeded.

| Scenario | Behavior |
|----------|----------|
| Count is 0 | Step skipped |
| Count is negative | Step errors |
| Jinja renders non-integer | Step errors |
| File not found | Step errors |
| File is empty | Step skipped |

---

## Error Handling

### The `on_failure` Object

```yaml
on_failure:
  action: retry
  notify: slack_alert
```

**`abort`** (default) — Stops the workflow immediately. For critical steps.

**`skip`** — Logs failure, continues to next step. For non-critical steps.

**`retry`** — Re-runs the step once. If it fails again, aborts. For transient issues.

**`notify`** — Names a hook from `HOOK_REGISTRY` called with the failed step's result before action is applied. Missing hooks are silently skipped.

---

## Hook System

### Defining Hooks

```python
from goblin.hooks import hook

@hook("run_tests")
def run_tests(tool_use):
    subprocess.run(["pytest"], check=True)
```

### Using Hooks in Blueprints

```yaml
defaults:
  hooks:
    post_tool: [run_tests, run_lint]

steps:
  - id: implement
    prompt: "Implement the feature"
    # inherits post_tool: [run_tests, run_lint]

  - id: docs_only
    prompt: "Update the docs"
    hooks:
      post_tool: [run_lint]
      # REPLACES defaults — run_tests will NOT run
```

**Override behavior:** Step-level hooks replace defaults entirely (no merge). If a step defines `hooks.post_tool`, the default is ignored for that step. If a step only specifies `pre_tool`, the default `post_tool` still applies.

**Project hooks:** `load_project` steps register shell command hooks as `project_pre_tool` / `project_post_tool`. Reference them like any other hook name.

---

## MCP Servers

MCP (Model Context Protocol) servers give steps access to external tools — GitHub, Jira, databases, custom APIs, etc. Define them at `defaults.mcp_servers` (available to all steps) or `steps[].mcp_servers` (per-step). Step-level servers merge with defaults; if both define the same server name, the step's config wins.

### Transport Types

Three transports are supported:

**stdio** — Launches a subprocess. The server binary communicates over stdin/stdout.

```yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "$GITHUB_TOKEN"
```

- `command` (required) — The executable to run (`npx`, `uvx`, `node`, etc.)
- `args` (optional) — Arguments passed to the command
- `type` (optional) — Inferred as `"stdio"` when `command` is present

**sse** — Connects to a remote server using Server-Sent Events.

```yaml
mcp_servers:
  my_api:
    type: sse
    url: "https://example.com/mcp"
    headers:
      Authorization: "Bearer $API_TOKEN"
```

- `type` (required) — Must be `"sse"`
- `url` (required) — SSE endpoint URL
- `headers` (optional) — HTTP headers sent with requests

**http** — Connects to a remote server using HTTP.

```yaml
mcp_servers:
  my_api:
    type: http
    url: "https://example.com/mcp"
    headers:
      Authorization: "Bearer $API_TOKEN"
```

- `type` (required) — Must be `"http"`
- `url` (required) — HTTP endpoint URL
- `headers` (optional) — HTTP headers sent with requests

### Secrets and Environment Variables

MCP server configs support `$VAR` and `${VAR}` syntax for referencing environment variables. These are resolved from `os.environ` at runtime (loaded via `.env` / `load_dotenv()`). If a referenced variable is not set, the runner raises an error.

**Never hardcode secrets in the blueprint.** Use `$VAR` references and instruct users to set the values in their `.env` file or shell environment.

```yaml
env:
  GITHUB_TOKEN: "$GITHUB_TOKEN"        # resolved from os.environ
  API_KEY: "${MY_API_KEY}"              # alternate syntax, same behavior
```

To set up secrets, add them to your `.env` file in the project root:

```
GITHUB_TOKEN=ghp_your_token_here
MY_API_KEY=sk-your-key-here
```

### Variable Interpolation

All string values in MCP server configs also support `{variable}` interpolation from the workflow context (vars + runtime context). Environment variables are resolved first, then `{variable}` interpolation is applied.

```yaml
vars:
  org: my-org
defaults:
  mcp_servers:
    github:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github", "--org", "{org}"]
      env:
        GITHUB_TOKEN: "$GITHUB_TOKEN"
```

### Allowing MCP Tools in Steps

MCP tools are namespaced as `mcp__<server_name>__<tool_name>`. Use a wildcard to allow all tools from a server, or specify individual tools:

```yaml
steps:
  - id: list_issues
    prompt: "List all open issues"
    allowed_tools:
      - mcp__github__*                  # all tools from the github server
    mcp_servers:
      github:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-github"]
        env:
          GITHUB_TOKEN: "$GITHUB_TOKEN"

  - id: update_ticket
    prompt: "Update the Jira ticket"
    allowed_tools:
      - mcp__jira__update_issue         # one specific tool
      - Read                            # can mix with regular tools
```

### Default vs Step-Level Servers

Define servers in `defaults` when multiple steps need them. Override or add servers at the step level when only one step needs a specific server.

```yaml
defaults:
  mcp_servers:
    github:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "$GITHUB_TOKEN"

steps:
  - id: read_issues
    prompt: "List all open issues"
    allowed_tools: [mcp__github__*]
    # inherits github server from defaults

  - id: update_jira
    prompt: "Update the Jira ticket"
    allowed_tools: [mcp__jira__*]
    mcp_servers:
      jira:                             # added for this step only
        command: npx
        args: ["-y", "@modelcontextprotocol/server-atlassian"]
        env:
          JIRA_API_TOKEN: "$JIRA_API_TOKEN"
          JIRA_URL: "$JIRA_URL"
          JIRA_EMAIL: "$JIRA_EMAIL"
```

### Common MCP Server Examples

**GitHub** — Requires a GitHub personal access token.

```yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "$GITHUB_TOKEN"
```

`.env`: `GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Jira (Atlassian)** — Requires an Atlassian API token, instance URL, and email.

```yaml
mcp_servers:
  jira:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-atlassian"]
    env:
      JIRA_API_TOKEN: "$JIRA_API_TOKEN"
      JIRA_URL: "$JIRA_URL"
      JIRA_EMAIL: "$JIRA_EMAIL"
```

`.env`:
```
JIRA_API_TOKEN=your_atlassian_api_token
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
```

**Filesystem** — No secrets required.

```yaml
mcp_servers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
```

**Remote server (SSE/HTTP)** — For self-hosted or third-party MCP endpoints.

```yaml
mcp_servers:
  my_service:
    type: sse
    url: "$MCP_SERVER_URL"
    headers:
      Authorization: "Bearer $MCP_API_KEY"
```

`.env`:
```
MCP_SERVER_URL=https://your-mcp-server.example.com/sse
MCP_API_KEY=your_api_key_here
```

---

## Common Patterns

### Clone, Load, Work

```yaml
version: v1
workflow:
  name: fix_issue
  vars:
    repo: https://github.com/org/service.git
    clone_dir: /tmp/service
  steps:
    - id: clone
      prompt: "Clone {repo} into {clone_dir}"
      allowed_tools: [Bash]
      on_failure:
        action: abort
    - id: load
      load_project: "{clone_dir}"
    - id: fix
      prompt: "Read the codebase and fix the failing tests"
      allowed_tools: [Bash, Read, Write, Edit, Glob, Grep]
```

### Conditional Chains

```yaml
- id: test
  prompt: "Run the test suite"
  allowed_tools: [Bash]
- id: deploy
  prompt: "Deploy to staging"
  allowed_tools: [Bash]
  condition: "{{ steps.test.success }}"
```

### Resilient Workflows

```yaml
- id: enrich
  prompt: "Fetch extra metadata from the API"
  allowed_tools: [Bash]
  on_failure:
    action: skip
- id: report
  prompt: "Write a summary report"
  allowed_tools: [Write]
```

### Iterate Over Items

```yaml
- id: find_items
  prompt: "Find all modules without tests and return them as a JSON array"
  allowed_tools: [Read, Glob, Grep]
- id: add_tests
  prompt: "Write unit tests for {item}"
  foreach: "{{ steps.find_items.output.items }}"
  allowed_tools: [Read, Write, Bash]
  on_failure:
    action: skip
```

---

## Anti-Patterns

- **Missing `load_project` before prompt** — Without it, the agent runs with no project awareness (no CLAUDE.md, no agents, no settings).
- **Unregistered hook names** — Silently skipped. Verify hooks are registered with `@hook("name")` in imported code.
- **Over-broad tool lists** — Giving every step all tools defeats tool scoping. Read-only steps: `[Read, Glob, Grep]`. Shell-only: `[Bash]`.
- **Relying on `depends_on`** — Schema-defined but not enforced. Use array order.
