# Plan: MCP Server Support in Blueprints

## Goal

Allow blueprints to declare MCP servers so that agents running prompt steps have access to external tools provided via the Model Context Protocol.

---

## Task 1: Add `_resolve_env_vars` utility (`goblin/utils/env.py`)

Create a new file `goblin/utils/env.py` with a single function:

```python
def resolve_env_vars(value):
```

**Behavior:**
- Recursively walk a nested structure (dicts, lists, strings).
- For every string value, replace occurrences of `$VAR` and `${VAR}` with `os.environ[VAR]`.
- Use a regex like `r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'` to match both forms.
- If a referenced env var is **not set**, raise a `ValueError` with a clear message like `"Environment variable 'VAR' is not set (referenced in MCP server config)"`.
- For dicts: return a new dict with keys unchanged and values resolved.
- For lists: return a new list with each element resolved.
- For strings: return the substituted string.
- For non-string scalars (int, bool, None): return as-is.

**No changes to `goblin/utils/__init__.py`** — the function will be imported directly from the module path.

---

## Task 2: Add `_resolve_env_vars` tests (`tests/test_resolve_env_vars.py`)

Create `tests/test_resolve_env_vars.py`. Use `monkeypatch.setenv` and `monkeypatch.delenv` for env var control.

**Test cases (one test method each):**

| Test | Input | Expected |
|------|-------|----------|
| `test_simple_dollar_var` | `"$FOO"` with `FOO=bar` | `"bar"` |
| `test_braced_var` | `"${FOO}"` with `FOO=bar` | `"bar"` |
| `test_var_embedded_in_string` | `"Bearer $TOKEN"` with `TOKEN=abc` | `"Bearer abc"` |
| `test_braced_var_embedded` | `"Bearer ${TOKEN}"` with `TOKEN=abc` | `"Bearer abc"` |
| `test_no_vars_unchanged` | `"plain string"` | `"plain string"` |
| `test_dict_values_resolved` | `{"key": "$FOO"}` with `FOO=bar` | `{"key": "bar"}` |
| `test_dict_keys_unchanged` | `{"$FOO": "val"}` with `FOO=bar` | `{"$FOO": "val"}` |
| `test_nested_dict` | `{"a": {"b": "$X"}}` with `X=y` | `{"a": {"b": "y"}}` |
| `test_list_values_resolved` | `["$A", "$B"]` with `A=1, B=2` | `["1", "2"]` |
| `test_non_string_passthrough` | `42`, `True`, `None` | `42`, `True`, `None` |
| `test_missing_env_var_raises` | `"$MISSING"` with MISSING unset | `ValueError` with `"MISSING"` in message |
| `test_missing_braced_var_raises` | `"${MISSING}"` with MISSING unset | `ValueError` with `"MISSING"` in message |
| `test_multiple_vars_in_one_string` | `"$A and ${B}"` with `A=x, B=y` | `"x and y"` |

---

## Task 3: Update the JSON schema (`spec/v1.json`)

### 3a. Add MCP server definitions to `definitions`

Add three new entries to the top-level `"definitions"` object (alongside `"hooks"` and `"step"`):

**`mcpServerStdio`:**
```json
{
  "type": "object",
  "description": "MCP server launched as a subprocess communicating via stdio",
  "required": ["command"],
  "additionalProperties": false,
  "properties": {
    "type": {
      "type": "string",
      "const": "stdio",
      "description": "Transport type. Optional for stdio servers — inferred when command is present.",
      "examples": ["stdio"]
    },
    "command": {
      "type": "string",
      "description": "The command to launch the MCP server process. Supports $VAR env var references.",
      "examples": ["npx", "uvx", "node"]
    },
    "args": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Arguments passed to the command. Supports $VAR env var references and {variable} interpolation.",
      "examples": [["-y", "@modelcontextprotocol/server-github"]]
    },
    "env": {
      "type": "object",
      "additionalProperties": { "type": "string" },
      "description": "Environment variables passed to the server process. Values support $VAR env var references.",
      "examples": [{ "GITHUB_TOKEN": "$GITHUB_TOKEN" }]
    }
  }
}
```

**`mcpServerSSE`:**
```json
{
  "type": "object",
  "description": "Remote MCP server using Server-Sent Events transport",
  "required": ["type", "url"],
  "additionalProperties": false,
  "properties": {
    "type": {
      "type": "string",
      "const": "sse",
      "description": "Transport type",
      "examples": ["sse"]
    },
    "url": {
      "type": "string",
      "description": "URL of the SSE MCP server endpoint. Supports $VAR and {variable} interpolation.",
      "examples": ["https://example.com/mcp"]
    },
    "headers": {
      "type": "object",
      "additionalProperties": { "type": "string" },
      "description": "HTTP headers sent with requests. Values support $VAR env var references.",
      "examples": [{ "Authorization": "Bearer $API_TOKEN" }]
    }
  }
}
```

**`mcpServerHTTP`:**
```json
{
  "type": "object",
  "description": "Remote MCP server using HTTP transport",
  "required": ["type", "url"],
  "additionalProperties": false,
  "properties": {
    "type": {
      "type": "string",
      "const": "http",
      "description": "Transport type",
      "examples": ["http"]
    },
    "url": {
      "type": "string",
      "description": "URL of the HTTP MCP server endpoint. Supports $VAR and {variable} interpolation.",
      "examples": ["https://example.com/mcp"]
    },
    "headers": {
      "type": "object",
      "additionalProperties": { "type": "string" },
      "description": "HTTP headers sent with requests. Values support $VAR env var references.",
      "examples": [{ "Authorization": "Bearer $API_TOKEN" }]
    }
  }
}
```

**`mcpServers`:**
```json
{
  "type": "object",
  "description": "Named MCP server configurations. Keys are server names, values are server configs. Supports stdio (subprocess), sse (Server-Sent Events), and http transports. All string values support $VAR env var references and {variable} context interpolation.",
  "additionalProperties": {
    "oneOf": [
      { "$ref": "#/definitions/mcpServerStdio" },
      { "$ref": "#/definitions/mcpServerSSE" },
      { "$ref": "#/definitions/mcpServerHTTP" }
    ]
  },
  "examples": [{
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" }
    }
  }]
}
```

### 3b. Add `mcp_servers` to `defaults`

In `definitions.step` → no. In `properties.workflow.properties.defaults.properties`, add:

```json
"mcp_servers": { "$ref": "#/definitions/mcpServers" }
```

### 3c. Add `mcp_servers` to step properties

In `definitions.step.properties`, add:

```json
"mcp_servers": { "$ref": "#/definitions/mcpServers" }
```

---

## Task 4: Add `_build_mcp_servers` to `V1Blueprint` (`goblin/blueprint.py`)

### 4a. Add import

At the top of `goblin/blueprint.py`, add:

```python
from goblin.utils.env import resolve_env_vars
```

### 4b. Add the method

Add a new method `_build_mcp_servers` to `V1Blueprint`, placed after `_build_hooks` (after line 70). Signature:

```python
def _build_mcp_servers(self, step: PromptStep, context: dict) -> dict:
```

**Logic:**

1. Read workflow defaults: `defaults = self.spec.get("defaults", {}).get("mcp_servers", {})`
2. Read step config: `step_servers = step.mcp_servers_config or {}`
3. Merge: `merged = {**defaults, **step_servers}` — step wins on name collision.
4. If `merged` is empty, return `{}`.
5. Call `resolve_env_vars(merged)` to resolve all `$VAR` / `${VAR}` references. This will raise `ValueError` if any env var is missing.
6. Apply `{variable}` interpolation from `context`: walk the resolved dict and call `.format(**context)` on every string value (same recursive walk pattern — could be a small local helper or reuse a shared one).
7. For any server where `type` is absent but `command` is present, no action needed — the SDK treats missing type as stdio.
8. Return the final `dict[str, McpServerConfig]`.

For step 6, write a local `_interpolate_strings(obj, context)` helper (similar structure to `resolve_env_vars` but calls `.format(**context)` on strings). Or, make `resolve_env_vars` more general. Simplest: a small private function in `blueprint.py`.

---

## Task 5: Update `PromptStep` (`goblin/step.py`)

### 5a. Store MCP config in `__init__`

In `PromptStep.__init__` (line 48–56), add after line 55 (`self.on_failure`):

```python
self.mcp_servers_config: dict | None = raw.get("mcp_servers")
```

### 5b. Pass MCP servers to `ClaudeAgentOptions` in `run()`

In `PromptStep.run()` (line 58–86), after the `if self._blueprint.project_agents:` block (line 73–74), add:

```python
mcp_servers = self._blueprint._build_mcp_servers(self, context)
if mcp_servers:
    kwargs["mcp_servers"] = mcp_servers
```

---

## Task 6: Add `_build_mcp_servers` tests (`tests/test_build_mcp_servers.py`)

Create `tests/test_build_mcp_servers.py`. Follow the same patterns as `tests/test_build_hooks.py`:

- Use `_make_blueprint` helper to construct a `V1Blueprint` from a raw spec dict.
- Test `_build_mcp_servers` by calling it directly on the blueprint instance with a constructed `PromptStep` and context dict.

**Helper:**

```python
def _make_blueprint(steps, defaults=None):
    spec = {"name": "test_wf", "steps": steps}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)
```

**Test classes and methods:**

### `TestBuildMcpServersDefaults`

| Test | Setup | Assert |
|------|-------|--------|
| `test_workflow_defaults_used_when_step_has_none` | `defaults={"mcp_servers": {"gh": {"command": "npx", "args": ["-y", "@mcp/github"]}}}`, step has no `mcp_servers` | Result contains `"gh"` with `command="npx"` |
| `test_empty_when_no_defaults_no_step` | No defaults, no step mcp_servers | Result is `{}` |

### `TestBuildMcpServersStepLevel`

| Test | Setup | Assert |
|------|-------|--------|
| `test_step_servers_returned` | Step has `mcp_servers: {"jira": {"type": "http", "url": "https://j.co/mcp"}}` | Result contains `"jira"` |
| `test_step_overrides_default_by_name` | Default has `{"gh": {"command": "old"}}`, step has `{"gh": {"command": "new"}}` | Result `"gh"` has `command="new"` |

### `TestBuildMcpServersMerge`

| Test | Setup | Assert |
|------|-------|--------|
| `test_merge_additive` | Default has `{"gh": {...}}`, step has `{"jira": {...}}` | Result has both `"gh"` and `"jira"` |

### `TestBuildMcpServersEnvResolution`

Use `monkeypatch.setenv` for all tests in this class.

| Test | Setup | Assert |
|------|-------|--------|
| `test_env_var_in_env_field` | Server has `env: {"TOKEN": "$MY_TOKEN"}`, `MY_TOKEN=secret` set | Result server env has `TOKEN=secret` |
| `test_env_var_in_url` | `url: "https://$HOST/mcp"`, `HOST=example.com` | `url` is `"https://example.com/mcp"` |
| `test_env_var_in_headers` | `headers: {"Auth": "Bearer $KEY"}`, `KEY=abc` | Header value is `"Bearer abc"` |
| `test_missing_env_var_raises` | `env: {"TOKEN": "$MISSING"}`, MISSING not set | Raises `ValueError` |

### `TestBuildMcpServersContextInterpolation`

| Test | Setup | Assert |
|------|-------|--------|
| `test_context_var_in_args` | `args: ["-y", "{package}"]`, context `{"package": "@mcp/gh"}` | Args contain `"@mcp/gh"` |
| `test_context_var_in_url` | `url: "https://{host}/mcp"`, context `{"host": "example.com"}` | URL is `"https://example.com/mcp"` |
| `test_mixed_env_and_context` | `url: "https://{host}/mcp"`, `headers: {"Auth": "Bearer $TOKEN"}`, context has `host`, env has `TOKEN` | Both resolved correctly |

### `TestBuildMcpServersTypeInference`

| Test | Setup | Assert |
|------|-------|--------|
| `test_stdio_inferred_when_command_present_no_type` | `{"command": "npx"}` (no `type` key) | Result includes the server as-is (no type added — SDK handles this) |

---

## Task 7: Add MCP parsing tests to `tests/test_parse_step.py`

Add a new test class `TestParseStepMcpServers` to the existing file:

| Test | Setup | Assert |
|------|-------|--------|
| `test_prompt_step_stores_mcp_servers` | Step has `mcp_servers: {"gh": {"command": "npx"}}` | `step.mcp_servers_config == {"gh": {"command": "npx"}}` |
| `test_prompt_step_mcp_servers_default_none` | Step has no `mcp_servers` | `step.mcp_servers_config is None` |

---

## Task 8: Add MCP flow-through test to `tests/test_run_orchestration.py`

This is trickier — `run()` orchestration uses mock steps, so we can't directly verify `ClaudeAgentOptions` kwargs. Instead, add a test to `TestRunVarsMerging` or a new class `TestRunMcpServersPassthrough` that verifies the `defaults.mcp_servers` config is stored on the blueprint and accessible:

| Test | Setup | Assert |
|------|-------|--------|
| `test_mcp_servers_in_defaults_accessible` | `_make_blueprint` with `defaults={"mcp_servers": {"gh": {"command": "npx"}}}` | `bp.spec["defaults"]["mcp_servers"]["gh"]["command"] == "npx"` |

The actual end-to-end wiring (blueprint → step → ClaudeAgentOptions) is covered by the `_build_mcp_servers` unit tests in Task 6 and the `PromptStep` attribute test in Task 7.

---

## Task 9: Add commented MCP example to `workflows/test.yaml`

Add a commented block at the bottom of the file showing the feature:

```yaml
# --- MCP server example (uncomment to use) ---
# To use MCP servers, add a .env file with your tokens:
#   GITHUB_TOKEN=ghp_...
#
# Then configure servers at workflow or step level:
#
#   defaults:
#     mcp_servers:
#       github:
#         command: npx
#         args: ["-y", "@modelcontextprotocol/server-github"]
#         env:
#           GITHUB_TOKEN: $GITHUB_TOKEN
#
#   steps:
#     - id: fetch_issues
#       prompt: "List open issues in {repo}"
#       allowed_tools: [mcp__github__list_issues]
```

---

## Task 10: Update CLAUDE.md project documentation

Update the `CLAUDE.md` file to reflect the new feature:

### 10a. Add to project layout

Add `goblin/utils/env.py` to the tree with description `# $VAR / ${VAR} env var resolution`.

### 10b. Add to blueprint fields table

Add a row:

| Field | Description | Status |
|-------|-------------|--------|
| `defaults.mcp_servers` | Named MCP server configs (stdio/sse/http) available to all steps | Implemented |
| `steps[].mcp_servers` | Per-step MCP servers; merges with defaults (step wins on name collision) | Implemented |

### 10c. Add to key methods

Add `_build_mcp_servers()` — Merge workflow + step MCP configs, resolve `$VAR` env refs, apply `{variable}` interpolation.

### 10d. Add to conventions

- MCP server secrets use `$VAR` / `${VAR}` syntax resolved from `os.environ` (loaded via `.env` / `load_dotenv()`)

---

## File Changes Summary

| File | Change |
|------|--------|
| `goblin/utils/env.py` | **New** — `resolve_env_vars()` function |
| `spec/v1.json` | Add `mcpServerStdio`, `mcpServerSSE`, `mcpServerHTTP`, `mcpServers` definitions; add `mcp_servers` to `defaults` and `step` |
| `goblin/blueprint.py` | Import `resolve_env_vars`; add `_build_mcp_servers()` method with `_interpolate_strings` helper |
| `goblin/step.py` | Add `self.mcp_servers_config` to `PromptStep.__init__`; pass resolved MCP servers to `ClaudeAgentOptions` in `run()` |
| `tests/test_resolve_env_vars.py` | **New** — 13 test cases for env var resolution |
| `tests/test_build_mcp_servers.py` | **New** — ~14 test cases for MCP server merging, env resolution, context interpolation |
| `tests/test_parse_step.py` | Add `TestParseStepMcpServers` class (2 tests) |
| `tests/test_run_orchestration.py` | Add 1 test for defaults accessibility |
| `workflows/test.yaml` | Add commented MCP example |
| `CLAUDE.md` | Document new feature in layout, fields table, methods, and conventions |

## Design Decisions

1. **Merge semantics for MCP servers** — Unlike hooks (which use full override), MCP servers merge workflow defaults with step-level config. Servers are independent resources and a step typically wants _additional_ servers, not a complete replacement. A step can override a default server by declaring one with the same name.

2. **No `sdk` type support** — The `sdk` transport requires a live Python `mcp.server.Server` instance, which cannot be expressed in YAML. Users who need programmatic MCP servers can extend the blueprint class directly.

3. **`$VAR` for secrets, `{variable}` for workflow data** — Two interpolation layers serve different purposes. `$VAR` resolves from `os.environ` (loaded from `.env` via `load_dotenv()`) for secrets. `{variable}` resolves from the context dict for workflow-level data. `$VAR` is resolved first so the context dict never sees raw secrets.

4. **Fail fast on missing env vars** — If a `$VAR` reference points to an unset environment variable, the blueprint raises immediately during MCP server resolution rather than letting the agent fail later with a cryptic connection error.

5. **`type` is optional for stdio** — When `command` is present and `type` is omitted, we infer `stdio`. This matches the SDK's convention where `type` is `NotRequired` on `McpStdioServerConfig`.
