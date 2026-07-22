# Your First Blueprint

A blueprint is a YAML file that tells Goblin what to do. This page walks you through writing a minimal blueprint from scratch.

## Minimal Blueprint

Create a file named `hello.yaml`:

```yaml
version: v1
blueprint:
  name: hello_world
  vars:
    name: Goblin

  phases:
    - id: greet
      prompt: "Say hello to {name}"
      allowed_tools: []
```

## Key Parts

- `version: v1` — The blueprint schema version. Required.
- `blueprint.name` — A human-readable identifier.
- `blueprint.vars` — Key/value pairs available for `{variable}` interpolation in prompts.
- `phases` — An ordered list of steps. Each phase must have an `id` and one of `prompt`, `load_project`, or `swarm`.
- `allowed_tools` — A whitelist of tools the agent may use. An empty list means the agent can only reply with text.

## Validate

Before running, check that your blueprint is valid:

```sh
goblin validate hello.yaml
```

If the file is valid, you will see:

```
hello.yaml: valid
```

If there are errors, the validator prints the JSON path and a human-readable message for each problem.

## Run

```sh
goblin run hello.yaml --context '{"name": "Alice"}'
```

The agent receives the prompt "Say hello to Alice" and returns a text response.
