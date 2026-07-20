# Validation

Goblin validates every blueprint against a JSON Schema before execution. The schema lives at `spec/v1.json` in the repository.

## The `validate` Command

Check one or more blueprints from the command line:

```sh
goblin validate my_blueprint.yaml
goblin validate blueprints/*.yaml
```

Output for a valid blueprint:

```
my_blueprint.yaml: valid
```

Output for an invalid blueprint:

```
my_blueprint.yaml: invalid
  - blueprint.phases[0].id: 'id' is a required property
  - blueprint.phases[1].prompt: 'prompt' is a required property
```

Each error includes:
- **path** — JSON path to the offending field (e.g., `blueprint.phases[0].id`)
- **message** — Human-readable error message
- **validator** — The schema keyword that failed (e.g., `required`, `type`)

## Schema Highlights

The JSON Schema enforces:

- Top-level `version` and `blueprint` keys are required.
- `blueprint.name` and `blueprint.phases` are required.
- Each phase must have an `id` and exactly one of `prompt`, `load_project`, or `swarm`.
- `allowed_tools` must be an array of strings.
- `on_failure.action` must be one of `skip`, `retry`, or `abort`.
- `condition` and `foreach` must be strings.
- `output_schema` must be a valid JSON Schema fragment.

## IDE Support

You can reference the schema in your blueprint files for validation and autocomplete in supported editors:

```yaml
# yaml-language-server: $schema=../spec/v1.json
version: v1
blueprint:
  ...
```

## Runtime Validation

Even if a blueprint passes schema validation, runtime errors can still occur:

- Missing environment variables (`$GOBLIN_MODEL_API_KEY`)
- Invalid file paths in `load_project` or `foreach`
- MCP servers that fail to start
- LLM API errors

These are reported in the JSONL logs and phase results.
