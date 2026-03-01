Run a goblin workflow blueprint.

Arguments: $ARGUMENTS

Parse the arguments to extract:
1. **Workflow path** (required) — the first positional argument, a path to a YAML blueprint file
2. **Context JSON** (optional) — the second positional argument, a JSON string to pass as context

Then run the workflow using the installed `goblin` CLI:

```
goblin <workflow_path> --context '<context_json>'
```

If no context JSON is provided, omit the `--context` flag entirely.

If the command fails, read the error output and help the user resolve it. Common issues:
- **File not found**: The workflow path doesn't exist — check the path and suggest corrections
- **Invalid JSON**: The context string is malformed — help fix the JSON syntax
- **goblin not found**: The CLI isn't installed — suggest running `/install` first
