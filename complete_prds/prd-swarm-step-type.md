# Swarm Step Type

## Overview

The swarm step type enables parallel agent execution within a single blueprint step. A team of concurrent OpenHands agents coordinates through a shared SQLite database and filesystem workspace to accomplish a high-level goal. A mandatory coordinator agent decomposes the goal into tasks, assigns them to worker agents, and evaluates completion across multiple rounds.

The swarm step is mutually exclusive with `prompt` and `load_project` — a step must have exactly one of the three.

## Blueprint Schema

A swarm step is defined under the `swarm` key on a step:

```yaml
steps:
  - id: build_feature
    swarm:
      goal: "Implement user authentication with login, signup, and password reset"
      workspace: ".goblin/swarms/{step_id}"   # default; supports {variable} interpolation

      coordinator:
        prompt: |
          Break down the goal into tasks. Assign each to an agent by writing
          rows to the `tasks` table. Monitor progress and mark the swarm
          complete when all tasks pass validation.
        allowed_tools: [Read]

      agents:
        - name: backend
          prompt: |
            You are a backend engineer. Check the tasks table for assignments
            targeting you. Implement each task, write files to the workspace,
            and update your task status when done.
          allowed_tools: [Bash, Read, Write, Edit]

        - name: frontend
          prompt: |
            You are a frontend engineer. Check the tasks table for assignments
            targeting you. Implement each task using the API contracts
            registered as artifacts by the backend agent.
          allowed_tools: [Bash, Read, Write, Edit]

      max_rounds: 5               # max coordinator evaluation rounds (default: 5)
      max_iterations: 50          # per-agent turn budget (default: 100)
      timeout: 600                # swarm-level timeout in seconds (default: none)
      cleanup: "on_success"       # on_success | always | never (default: on_success)

    on_failure:
      action: abort
```

### Schema Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `swarm.goal` | string | yes | — | High-level objective injected into every agent's system prompt |
| `swarm.workspace` | string | no | `.goblin/swarms/{step_id}` | Path for shared files and SQLite DB. Supports `{variable}` interpolation from context. Relative paths resolve against `project_path` or `cwd`. |
| `swarm.coordinator` | object | yes | — | The coordinator agent definition |
| `swarm.coordinator.prompt` | string | yes | — | Coordinator's instruction prompt. Supports `{variable}` interpolation. |
| `swarm.coordinator.allowed_tools` | string[] | no | — | Tool whitelist for the coordinator. `Bash` is always appended if not present (required for the `swarmdb` CLI). |
| `swarm.agents` | array | yes | — | List of worker agent definitions (minimum 1) |
| `swarm.agents[].name` | string | yes | — | Unique agent identifier within the swarm |
| `swarm.agents[].prompt` | string | yes | — | Agent instruction prompt. Supports `{variable}` interpolation. |
| `swarm.agents[].allowed_tools` | string[] | no | — | Tool whitelist for the agent. `Bash` is always appended if not present. |
| `swarm.agents[].hooks` | object | no | — | Per-agent hook overrides (replaces step-level hooks for that agent) |
| `swarm.agents[].mcp_servers` | object | no | — | Per-agent MCP servers (merged with defaults and step-level servers) |
| `swarm.max_rounds` | integer | no | 5 | Maximum coordinator evaluation rounds before forced teardown |
| `swarm.max_iterations` | integer | no | 100 | Maximum turns per agent per round (passed as `max_turns` to the SDK) |
| `swarm.timeout` | integer | no | none | Swarm-level timeout in seconds. When exceeded, forces transition to teardown. |
| `swarm.cleanup` | string | no | `on_success` | Workspace cleanup policy: `on_success`, `always`, or `never` |

## Architecture

### Implementation Files

| File | Purpose |
|---|---|
| `goblin/swarm.py` | `SwarmStep` class — step protocol implementation, state machine orchestrator, agent/coordinator launching |
| `goblin/swarm_db.py` | `SwarmDB` class — SQLite coordination layer with validated reads/writes and state management |
| `goblin/enums.py` | `SwarmState`, `TaskStatus`, `AgentStatus`, `CleanupPolicy` StrEnum types |
| `goblin/prompts.py` | `SWARM_AGENT_PROMPT` and `SWARM_COORDINATOR_PROMPT` templates |
| `spec/v1.json` | JSON Schema definitions for `swarm`, `swarmCoordinator`, `swarmAgent` |

### SwarmStep Class

`SwarmStep` implements `StepProtocol` with attributes `id`, `condition`, `foreach`, and `on_failure`. It is instantiated by `V1Blueprint._parse_step()` when a step dict contains the `swarm` key.

The constructor extracts all configuration from the raw step dict:

- `goal`, `workspace_template`, `coordinator_config`, `agents_config` from the `swarm` key
- `max_rounds`, `max_iterations`, `timeout`, `cleanup` with their defaults
- `hooks_config` and `mcp_servers_config` from step-level fields
- A reference to the parent `V1Blueprint` for accessing hook building, MCP server merging, project refs, and project path

### Enums

All swarm constants use `StrEnum` so values compare equal to plain strings — SQLite reads work without explicit conversion.

```python
class SwarmState(StrEnum):
    SETUP          = "setup"
    COORDINATOR    = "coordinator"
    AGENTS_RUNNING = "agents_running"
    EVALUATING     = "evaluating"
    PAUSED         = "paused"
    TEARDOWN       = "teardown"

class TaskStatus(StrEnum):
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    DONE        = "done"
    FAILED      = "failed"

class AgentStatus(StrEnum):
    PENDING     = "pending"
    RUNNING     = "running"
    DONE        = "done"
    FAILED      = "failed"
    INTERRUPTED = "interrupted"

class CleanupPolicy(StrEnum):
    ON_SUCCESS = "on_success"
    ALWAYS     = "always"
    NEVER      = "never"
```

## Coordination Layer: SQLite

### SwarmDB

`SwarmDB` wraps a SQLite connection at `{workspace}/swarm.db` with WAL journal mode enabled. It provides two categories of operations:

**Agent-facing operations** (validated — agents can only touch `tasks`, `artifacts`, `signals`):

- `query(sql)` — Execute a `SELECT` statement and return rows as list of dicts. Rejects non-SELECT statements.
- `execute(sql)` — Execute an `INSERT`, `UPDATE`, or `DELETE` on allowed tables only (`tasks`, `artifacts`, `signals`). Rejects writes to `swarm_state` or `agent_state`. Returns affected row count.

**Orchestrator operations** (used by `SwarmStep` internally):

- `init_schema()` — Creates all five tables if they don't exist and seeds the initial `swarm_state` row.
- `get_state()` / `transition(new_state)` — Read and update the singleton `swarm_state` row.
- `increment_round()` — Atomically increment the round counter and return the new value.
- `register_agent(name)` — Insert a row into `agent_state` with `pending` status.
- `update_agent_status(name, status, iterations=None)` — Update an agent's status and optionally its iteration count.
- `get_tasks()` / `get_artifacts()` — Read all rows from the respective tables.
- `close()` — Close the database connection.

### Database Schema

```sql
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    assigned_to TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER,
    agent       TEXT,
    name        TEXT,
    path        TEXT,
    content     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT,
    to_agent    TEXT,
    type        TEXT,
    payload     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE swarm_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    state       TEXT NOT NULL DEFAULT 'setup',
    round       INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE agent_state (
    name        TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'pending',
    iterations  INTEGER DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Note: the `swarm_state` table does not have a `paused_from` column. The `PAUSED` state exists in the enum and state machine but there is no mechanism to enter it — no external signal or API triggers pause. If the state machine encounters `PAUSED`, it returns `{"success": False, "message": ["Swarm paused"], "paused": True}`.

### Agent Access: CLI Script

Agents interact with the database through a **CLI wrapper script** (`swarmdb`) deployed into the workspace at setup time. This is a self-contained Python script that agents invoke via Bash:

```bash
# Read
{workspace}/swarmdb query "SELECT * FROM tasks"

# Write
{workspace}/swarmdb execute "INSERT INTO tasks (title, description, assigned_to, status) VALUES ('Build API', 'REST endpoints', 'backend', 'pending')"
```

The script:
- Reads the database path from the `SWARMDB_PATH` environment variable (set automatically via `env` on all agent processes)
- Validates that `query` only accepts `SELECT` statements
- Validates that `execute` only accepts `INSERT`/`UPDATE`/`DELETE` on the three allowed tables (`tasks`, `artifacts`, `signals`)
- Connects with WAL mode and `Row` factory for dict-like access
- Returns JSON for queries, confirmation text for writes

This approach was chosen over an MCP server to avoid adding MCP context overhead to running agents.

## Coordination Layer: Filesystem

The workspace directory is created at setup with this structure:

```
{workspace}/
├── swarm.db              # SQLite coordination database
├── swarmdb               # CLI wrapper script (executable)
├── shared/               # Files any agent can read/write
└── agents/
    ├── backend/          # Per-agent scratch space
    ├── frontend/
    └── ...
```

Directory conventions are enforced via system prompt guidance, not filesystem restrictions. Agents are instructed to use `shared/` for cross-agent visibility and `agents/{name}/` for scratch work.

## Execution Model: State Machine

### States

```
SETUP → COORDINATOR → AGENTS_RUNNING → EVALUATING ──→ TEARDOWN
              ↑                            │
              └────────────────────────────┘  (round < max_rounds)
```

| State | Description |
|---|---|
| `setup` | Create workspace, initialize DB, register agents, deploy CLI script. Automatic transition to `coordinator`. |
| `coordinator` | Run the coordinator agent. It creates/updates tasks and assigns them to agents. If coordinator returns `{"done": true}`, transition to `teardown`. Otherwise, transition to `agents_running`. |
| `agents_running` | All worker agents execute concurrently via `asyncio.TaskGroup`. Each agent runs with its prompt, swarm context, and the `swarmdb` CLI available via Bash. Transitions to `evaluating` when all agents exit. |
| `evaluating` | Increment the round counter. If `round >= max_rounds`, transition to `teardown`. Otherwise, transition back to `coordinator` for re-evaluation. |
| `paused` | Defined in the enum and handled in the state machine loop but not reachable — no mechanism currently triggers a pause transition. Returns early with `{"success": False, "paused": True}`. |
| `teardown` | Collect final state from `tasks` and `artifacts` tables, build the result dict, close the DB, apply the cleanup policy. Terminal state. |

### `SwarmStep.run()` — Orchestrator Loop

```python
async def run(self, context):
    self._setup(context)

    deadline = None
    if self.timeout:
        deadline = asyncio.get_event_loop().time() + self.timeout

    while True:
        state = self._db.get_state()

        if deadline and asyncio.get_event_loop().time() > deadline:
            self._db.transition(SwarmState.TEARDOWN)
            continue

        match state:
            case SwarmState.SETUP:
                self._db.transition(SwarmState.COORDINATOR)
            case SwarmState.COORDINATOR:
                result = await self._run_coordinator(context)
                if result.get("done"):
                    self._db.transition(SwarmState.TEARDOWN)
                else:
                    self._db.transition(SwarmState.AGENTS_RUNNING)
            case SwarmState.AGENTS_RUNNING:
                await self._run_agents(context)
                self._db.transition(SwarmState.EVALUATING)
            case SwarmState.EVALUATING:
                round_num = self._db.increment_round()
                if round_num >= self.max_rounds:
                    self._db.transition(SwarmState.TEARDOWN)
                else:
                    self._db.transition(SwarmState.COORDINATOR)
            case SwarmState.PAUSED:
                return {"success": False, "message": ["Swarm paused"], "paused": True}
            case SwarmState.TEARDOWN:
                return self._teardown()
```

### Setup

`_setup(context)` performs:

1. Resolve the workspace path — interpolate `{variable}` and `{step_id}`, resolve relative paths against `project_path` or `cwd`
2. Create the directory structure: `shared/`, `agents/{name}/` for each worker
3. Initialize `SwarmDB` at `{workspace}/swarm.db` and create the schema
4. Register each agent in the `agent_state` table with `pending` status
5. Write the `swarmdb` CLI script to the workspace and make it executable
6. Transition state from `setup` to `coordinator`

### Coordinator Execution

`_run_coordinator(context)`:

1. Build a composite system prompt: `BASE_SYSTEM_PROMPT` + `SWARM_COORDINATOR_PROMPT` (with `{goal}`, `{agent_roster}`, `{workspace}` interpolated) + project refs if available
2. Interpolate `{variable}` in the coordinator's prompt
3. Ensure `Bash` is in the allowed tools list (needed for the `swarmdb` CLI)
4. Build hooks and MCP servers via the blueprint
5. Run the agent with the OpenHands SDK query method, setting `env={"SWARMDB_PATH": "..."}` and optionally `cwd` to the project path
6. Parse the last text block as JSON. If it contains `"done": true`, the swarm proceeds to teardown. Otherwise, agents run.

The coordinator prompt instructs it to:
- Break the goal into tasks and assign them to agents via `swarmdb execute`
- Check progress via `swarmdb query`
- Respond with `{"done": true, "message": [...]}` when complete or `{"done": false, "message": [...]}` when more work is needed

### Agent Execution

`_run_agents(context)` launches all worker agents concurrently using `asyncio.TaskGroup`.

`_run_single_agent(agent_cfg, context)`:

1. Mark agent status as `running` in `agent_state`
2. Build a composite system prompt: `BASE_SYSTEM_PROMPT` + `SWARM_AGENT_PROMPT` (with `{agent_name}`, `{goal}`, `{workspace}`, `{agent_roster}`) + project refs
3. Interpolate `{variable}` in the agent's prompt
4. Ensure `Bash` is in the allowed tools list
5. Resolve hooks — use per-agent hooks if specified, otherwise fall back to step-level hooks
6. Merge MCP servers — defaults → step → per-agent (per-agent wins on collision)
7. Run with `max_turns` set to `max_iterations`
8. On success: mark agent as `done` in `agent_state` with iteration count
9. On exception: mark agent as `failed`, print the error, return empty string

The agent prompt instructs each agent to:
- Use the `swarmdb` CLI to claim tasks, update status, register artifacts, and send/check signals
- Write outputs to `{workspace}/shared/` and scratch work to `{workspace}/agents/{agent_name}/`
- Respond with final status when all assigned work is complete

### Teardown

`_teardown()`:

1. Query all tasks and artifacts from the database
2. Determine success: `True` if all tasks have `status == "done"` (or no tasks exist)
3. Build message list from task statuses (e.g., `"[done] Build API"`, `"[failed] Write tests"`)
4. Close the database connection
5. Apply cleanup policy:
   - `on_success`: delete workspace only if `success` is `True`
   - `always`: always delete workspace
   - `never`: preserve workspace for inspection

### Result Structure

```python
{
    "success": bool,          # True if all tasks reached "done" status
    "message": [str],         # Status line per task, e.g. "[done] Build API"
    "tasks": [                # Full rows from the tasks table
        {"id": 1, "title": "...", "status": "done", "assigned_to": "backend", ...}
    ],
    "artifacts": [            # Full rows from the artifacts table
        {"name": "api_schema", "agent": "backend", "path": "shared/api.json", ...}
    ]
}
```

Results are stored in `results[step_id]` as usual, accessible to subsequent steps via conditions and Jinja2 expressions.

## Integration with Existing Systems

| System | Integration |
|---|---|
| **Hooks** | Step-level `hooks` apply to all agents. Per-agent `hooks` override (not merge) the step-level hooks for that agent. Both resolve through `blueprint._build_hooks()`. |
| **MCP Servers** | Merged from defaults → step → per-agent. Per-agent servers have `$VAR` env resolution and `{variable}` interpolation applied. Step wins on name collision with defaults; per-agent wins on collision with step. |
| **Conditions** | `condition` on swarm steps works identically to other steps — evaluated by the blueprint before running the swarm. |
| **Foreach** | Supported — runs the entire swarm N times (tested with mock steps in `test_swarm.py`). |
| **on_failure** | Applied at the swarm step level by the blueprint's failure handler. Individual agent failures are tracked in `agent_state`; task-level failures surface through the `tasks` table. The swarm step itself fails if not all tasks reached `done`. |
| **Context/vars** | All workflow vars and context available for `{variable}` interpolation in swarm prompts, workspace path, coordinator prompt, and agent prompts. |
| **`load_project`** | If a prior step used `load_project`, the swarm inherits `project_path` (used as `cwd`), `project_refs` (appended to system prompts), and `project_settings_path`. |
| **Blueprint parsing** | `V1Blueprint._parse_step()` dispatches to `SwarmStep` when the step dict contains a `swarm` key. |

## What Is Not Implemented

The following items exist in the original PRD design or enum definitions but are not functional in the current implementation:

- **Pause/resume**: The `PAUSED` state and `SwarmState.PAUSED` enum value exist, but there is no mechanism (CLI command, hook, external signal) to trigger a pause transition. The `swarm_state` table lacks a `paused_from` column. If somehow entered, the loop returns immediately.
- **`paused_from` tracking**: The `swarm_state` table does not have this column, so resume-to-previous-state is not supported.
- **CHECK constraints on SQLite tables**: The PRD specified CHECK constraints on `status` columns in `tasks`, `swarm_state`, and `agent_state`. The actual schema uses plain `TEXT` columns without constraints. Validation happens in Python (SwarmDB methods and the CLI script).
- **`FOREIGN KEY` on artifacts**: The PRD specified `artifacts.task_id REFERENCES tasks(id)`. The actual schema has `task_id INTEGER` without a foreign key constraint.
- **SQL CHECK on agent status `interrupted`**: The `INTERRUPTED` enum value exists but the database has no constraint — it's enforced at the Python level.
- **State persistence for restart**: While state is written to SQLite, the `run()` method always calls `_setup()` first which re-initializes the workspace. There is no resume-from-existing-DB logic.
