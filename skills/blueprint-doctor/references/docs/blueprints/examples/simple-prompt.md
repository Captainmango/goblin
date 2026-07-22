# Example: Simple Prompt

A single-phase blueprint that asks an agent a question with no tools.

```yaml
version: v1
blueprint:
  name: simple_prompt
  vars:
    topic: "quantum computing"

  phases:
    - id: explain
      prompt: "Explain {topic} in one paragraph."
      allowed_tools: []
```

Run it:

```sh
goblin run simple-prompt.yaml --context '{"topic": "neural networks"}'
```

Because `allowed_tools` is empty, the agent can only reply with text. This is useful for summarisation, explanation, or brainstorming tasks.
