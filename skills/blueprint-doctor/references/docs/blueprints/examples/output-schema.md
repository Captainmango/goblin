# Example: Output Schema

This blueprint constrains the agent's response to a specific JSON structure.

```yaml
version: v1
blueprint:
  name: structured_response

  phases:
    - id: analyze
      prompt: |
        Analyze the following code snippet and return a JSON object
        with a "issues" array and a "score" number.
      allowed_tools: []
      output_schema:
        type: object
        properties:
          issues:
            type: array
            items:
              type: object
              properties:
                line:
                  type: integer
                severity:
                  type: string
                  enum: [low, medium, high]
                message:
                  type: string
              required: [line, severity, message]
          score:
            type: number
            minimum: 0
            maximum: 100
        required: [issues, score]
```

## How It Works

When `output_schema` is provided, Goblin:

1. Extracts JSON from the agent's text response.
2. Validates it against the schema using Draft 7 validation.
3. If validation fails, the phase returns `success: false` with a list of schema errors.

If the agent returns malformed JSON, Goblin attempts up to 3 automatic repair retries using the LLM before failing the phase.

## Response Format

The agent is instructed to return JSON by the base system prompt. You do not need to remind it in your prompt, though you may include example output shapes for clarity.
