# Environment Variables

Goblin loads environment variables from a `.env` file in the current working directory before resolving configuration.

## Required Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `GOBLIN_MODEL_API_KEY` | goblin | LLM API key. Falls back to `MODEL_API_KEY` if not set. Required when running blueprints that use an LLM. |

## Optional Variables

| Variable | Used By | Default | Purpose |
|----------|---------|---------|---------|
| `GOBLIN_BASE_URL` | goblin | `OPENHANDS_BASE_URL` | Base URL for the LLM API. |
| `GOBLIN_DEFAULT_MODEL` | goblin | — | Default model identifier. Also backs the structured-output coercion LLM for ACP phases. |
| `OPENHANDS_SUPPRESS_BANNER` | goblin | `1` | Suppress the OpenHands startup banner. |
| `GOBLIN_BACKEND` | goblin | — | Swarm backend selection (`turso` or `sqlite`). |
| `GOBLIN_TURSO_URL` | goblin | — | Turso/libSQL URL for distributed swarm state. |
| `GOBLIN_TURSO_AUTH_TOKEN` | goblin | — | Turso auth token. |
| `GOBLIN_TURSO_MODE` | goblin | — | Turso connection mode (`local` or `remote`). |
| `GOBLIN_TURSO_LOCAL_PATH` | goblin | — | Local libSQL database path. |
| `GOBLIN_LOG_CONFIG` | goblin | — | Path to a YAML logging configuration file. |
| `ANTHROPIC_API_KEY` | ACP harness (`claude-code`) | Provider API key for the Claude Code ACP harness. |
| `OPENAI_API_KEY` | ACP harness (`codex`) | Provider API key for the OpenAI Codex ACP harness. |
| `GEMINI_API_KEY` | ACP harness (`gemini-cli`) | Provider API key for the Gemini CLI ACP harness. |

## Example `.env`

```env
GOBLIN_MODEL_API_KEY=sk-...
```

## Loading Behaviour

- Goblin loads `.env` from the **current working directory only**.
- It does not walk parent directories.
- Variables already set in the process environment are not overwritten.
