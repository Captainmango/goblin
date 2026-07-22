# Variables and Interpolation

Goblin supports three forms of dynamic value insertion in blueprints:

1. **`{variable}` interpolation** — Python `str.format()` style.
2. **`$VAR` environment variable resolution** — Reads from the process environment.
3. **Jinja2 expressions** — Used in `condition` and `foreach` fields.

## `{variable}` Interpolation

Blueprint `vars` and `--context` values are injected into phase prompts and other string fields using Python `{variable}` syntax:

```yaml
blueprint:
  name: example
  vars:
    repo: https://github.com/example/repo.git

  phases:
    - id: clone
      prompt: "Clone {repo} into /tmp/project"
      allowed_tools: [Bash]
```

Runtime context overrides `vars`:

```sh
goblin run example.yaml --context '{"repo": "https://github.com/other/repo.git"}'
```

Interpolation is applied recursively to all string values in:
- Phase prompts
- `load_project` paths
- `workspace` configuration
- `mcp_servers` arguments and environment variables
- `foreach` file paths

## `$VAR` Environment Variables

Environment variables are resolved in fields that accept configuration values:

```yaml
defaults:
  llm:
    api_key: $GOBLIN_MODEL_API_KEY
    base_url: $GOBLIN_BASE_URL
```

Goblin loads `.env` from the current working directory before resolving variables.

## Jinja2 in `condition` and `foreach`

The `condition` and `foreach` fields support Jinja2 expressions evaluated against prior phase results.

### Available Variables

- `steps.<id>.success` — Boolean success status of a prior phase.
- `steps.<id>.message` — List of message strings from a prior phase.

### Custom Filters

- `contains(text)` — Returns `True` if any message contains the substring.
- `json_value(key)` — Parses messages as JSON dicts and returns the value at a dot-separated key path.

### Condition Examples

```yaml
phases:
  - id: lint
    prompt: "Run the linter"
    allowed_tools: [Bash]

  - id: fix
    prompt: "Fix any lint errors"
    allowed_tools: [Bash, Read, Edit]
    condition: "{{ not steps.lint.success }}"

  - id: deploy
    prompt: "Deploy to production"
    allowed_tools: [Bash]
    condition: "{{ steps.lint.success }}"
```

```yaml
  - id: retry_deploy
    prompt: "Retry the deployment"
    allowed_tools: [Bash]
    condition: "{{ steps.deploy.message | contains('connection timeout') }}"
```

```yaml
  - id: parse_output
    prompt: "Process the API response"
    allowed_tools: [Bash]
    condition: "{{ steps.api_call.message | json_value('data.status') == 'ok' }}"
```

### Foreach Examples

Run a phase N times:

```yaml
  - id: process_batch
    prompt: "Process batch item {index}"
    allowed_tools: [Bash]
    foreach: "5"
```

Run a phase once per line in a file:

```yaml
  - id: process_lines
    prompt: "Process line: {item}"
    allowed_tools: [Bash]
    foreach: "items.txt"
```

Dynamic count from prior phase output:

```yaml
  - id: process_tickets
    prompt: "Process ticket {index}"
    allowed_tools: [Bash]
    foreach: "{{ steps.fetch_tickets.output.count }}"
```
