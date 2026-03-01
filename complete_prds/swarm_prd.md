# PRD: Swarm Step Type

## Problem

Goblin currently executes steps sequentially — one agent at a time. Complex goals that benefit from parallel specialization (e.g., "build a full-stack feature with tests") require manual decomposition into sequential steps with brittle inter-step dependencies. There is no mechanism for multiple agents to work concurrently toward a shared goal, communicate progress, or hand off artifacts.

## Proposal

Add a **`swarm`** step type — mutually exclusive with `prompt` and `load_project` — that launches a team of concurrent agents coordinating through a shared filesystem workspace and a SQLite database for structured signalling.

## Goals

- Enable parallel agent execution within a single blueprint step
- Provide a structured coordination layer (SQLite) for task claiming, status updates, and artifact registration
- Provide an unstructured coordination layer (filesystem) for code, configs, and other artifacts
- Keep the design consistent with Goblin's existing step/hook/context architecture
- Make swarm behavior observable and debuggable

## Non-Goals

- Real-time inter-agent streaming or message passing (agents poll SQLite)
- Automatic task decomposition by AI (the coordinator agent decomposes the goal; blueprint authors steer via the coordinator prompt)
- Cross-step swarm persistence (each swarm step is self-contained)
- Agent-to-agent sub-agent delegation (agents coordinate through SQLite signalling, not direct invocation; may be revisited in a future version)

---

## Blueprint Schema

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
        allowed_tools: [Read, Write]

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

        - name: tests
          prompt: |
            You are a QA engineer. Watch for completed tasks and write
            integration tests. Register test results as artifacts.
          allowed_tools: [Bash, Read, Write, Edit]

      max_rounds: 5               # max coordinator evaluation rounds (default: 5)
      max_iterations: 50          # per-agent turn budget (default: 100)
      timeout: 600                # swarm-level timeout in seconds (default: none)
      cleanup: "on_success"       # on_success | always | never (default: on_success)

    on_failure:
      action: abort
```

### Schema Additions to `spec/v1.json`

The `swarm` key becomes a third option in the step `oneOf`:

| Field | Type | Required | Description |
|---|---|---|---|
| `swarm.goal` | string | yes | High-level objective injected into every agent's system prompt |
| `swarm.workspace` | string | no | Path for shared files and SQLite DB. Default: `.goblin/swarms/{step_id}` |
| `swarm.coordinator` | object | yes | The coordinator agent — decomposes the goal into tasks, assigns them to agents, and evaluates completion after each round |
| `swarm.coordinator.prompt` | string | yes | Coordinator's instruction prompt. Use this to steer task decomposition strategy. |
| `swarm.coordinator.allowed_tools` | string[] | no | Tool whitelist for coordinator |
| `swarm.agents` | array | yes | List of worker agent definitions |
| `swarm.agents[].name` | string | yes | Unique agent identifier within the swarm |
| `swarm.agents[].prompt` | string | yes | Agent instruction prompt |
| `swarm.agents[].allowed_tools` | string[] | no | Tool whitelist for the agent |
| `swarm.agents[].hooks` | object | no | Per-agent hook overrides |
| `swarm.agents[].mcp_servers` | object | no | Per-agent MCP server overrides |
| `swarm.max_rounds` | integer | no | Max coordinator evaluation rounds (default: 5) |
| `swarm.max_iterations` | integer | no | Max turns per agent per round (default: 100) |
| `swarm.timeout` | integer | no | Swarm-level timeout in seconds |
| `swarm.cleanup` | string | no | Workspace cleanup policy: `on_success`, `always`, or `never` (default: `on_success`) |

---

## Enums

All swarm constants use `StrEnum` for type safety with zero serialization friction — values compare equal to plain strings, so SQLite reads work without explicit conversion. This is a deliberate style choice that will be extended to the rest of the codebase over time.

```python
from enum import StrEnum

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

These enums are the single source of truth for valid values. The SQLite `CHECK` constraints mirror them, and the orchestrator's `match` statements exhaustively cover all variants.

---

## Coordination Layer: SQLite

A SQLite database is created at `{workspace}/swarm.db` before any agents start. Goblin pre-creates the schema and injects the database path into each agent's context as `{swarm_db}`.

### Schema

```sql
-- Tasks: units of work created by the coordinator agent
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    assigned_to TEXT,                              -- agent name
    status      TEXT DEFAULT 'pending'             -- pending | in_progress | done | failed
                CHECK (status IN ('pending', 'in_progress', 'done', 'failed')),
    result      TEXT,                              -- freeform outcome / error message
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Artifacts: registered outputs that other agents can discover
CREATE TABLE artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(id),
    agent       TEXT NOT NULL,                     -- producing agent name
    name        TEXT NOT NULL,                     -- logical name (e.g., "api_schema")
    path        TEXT,                              -- filesystem path relative to workspace
    content     TEXT,                              -- inline content (for small artifacts)
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Signals: lightweight messages between agents
CREATE TABLE signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT,                              -- NULL = broadcast
    type        TEXT NOT NULL,                     -- e.g., "ready", "blocked", "review_needed"
    payload     TEXT,                              -- JSON blob
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Agent Interaction with SQLite

Agents interact with the database through a **built-in `SwarmDB` tool** provided by Goblin. This is a lightweight tool registered on each agent that wraps SQLite operations — not an MCP server. This avoids adding MCP context overhead to running agents while still giving Goblin the ability to intercept, validate, and log all database operations.

The `SwarmDB` tool exposes operations aligned with the schema:

```
swarm_db.query(sql)          # Run a read query, returns rows
swarm_db.execute(sql)        # Run a write statement (INSERT/UPDATE)
```

Goblin validates that write operations target only the `tasks`, `artifacts`, and `signals` tables, and that status values conform to the CHECK constraint.

Each agent's system prompt includes:

- Instructions for using the `SwarmDB` tool
- The schema definition
- Conventions for claiming tasks (`UPDATE tasks SET status='in_progress', assigned_to='{agent_name}' WHERE ...`)
- How to register artifacts and send signals

---

## Coordination Layer: Filesystem

The workspace directory (`{workspace}/`) is the shared root. Convention:

```
{workspace}/
├── swarm.db              # SQLite coordination database
├── shared/               # Files any agent can read/write
└── agents/
    ├── backend/          # Per-agent scratch space
    ├── frontend/
    └── tests/
```

Agents are instructed to write outputs to `shared/` for cross-agent visibility and to their own `agents/{name}/` for scratch work. These are conventions enforced via system prompt guidance, not hard filesystem restrictions.

---

## Execution Model

### Implementation: `SwarmStep`

A new class implementing `StepProtocol`, analogous to `PromptStep` and `LoadProjectStep`.

### State Machine

The swarm orchestrator is driven by a state machine. All state is persisted to SQLite, making the swarm durable across process restarts and enabling pause/resume.

#### States

```
SETUP → COORDINATOR → AGENTS_RUNNING → EVALUATING ──→ TEARDOWN
              ↑              │  ↑            │
              └──────────────│──│────────────┘  (new tasks created,
                             ↓  │                round < max_rounds)
                          PAUSED ┘
                             │
                             ├──→ (resume) → previous state
                             └──→ (abort)  → TEARDOWN
```

| State | Description |
|---|---|
| `setup` | Create workspace, initialize DB, register tools. Automatic transition to `coordinator`. |
| `coordinator` | Run the coordinator agent. It creates/updates tasks and assigns them to agents. Transitions to `agents_running`. |
| `agents_running` | All agents execute concurrently. Transitions to `evaluating` when all agents exit. Can be interrupted → `paused`. |
| `evaluating` | Re-invoke coordinator to assess completion. If new tasks created and `round < max_rounds`, transition to `coordinator`. If done, transition to `teardown`. |
| `paused` | Swarm is suspended. Stores `paused_from` to know which state to resume into. Can resume or abort → `teardown`. |
| `teardown` | Collect results, apply cleanup policy, return step result. Terminal state. |

#### State Persistence

Two additional tables in `swarm.db` track orchestrator and agent-level state:

```sql
-- Singleton row tracking the swarm orchestrator state
CREATE TABLE swarm_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    state       TEXT NOT NULL
                CHECK (state IN ('setup', 'coordinator', 'agents_running',
                                 'evaluating', 'paused', 'teardown')),
    round       INTEGER DEFAULT 0,
    paused_from TEXT,                                 -- state to resume into
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Per-agent execution tracking
CREATE TABLE agent_state (
    name        TEXT PRIMARY KEY,
    status      TEXT DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'done', 'failed', 'interrupted')),
    iterations  INTEGER DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

This enables:

- **Durable state**: If the process dies, reading `swarm_state` on restart reveals exactly where to resume.
- **Pause/resume**: An external signal (CLI, hook, future UI) writes `state='paused'` + `paused_from`. The orchestrator loop picks this up and yields control. On resume, it transitions back to `paused_from`.
- **Observability**: Any external tool can query `swarm_state` and `agent_state` to see live progress — a foundation for dashboards and notifications.
- **Partial retry**: `agent_state` tracks which agents completed vs. were interrupted, enabling re-runs of only failed agents rather than the entire swarm.

#### Orchestrator Loop

```python
async def run(self, context):
    self._setup(context)

    while True:
        state = self._get_state()

        match state:
            case SwarmState.SETUP:
                self._transition(SwarmState.COORDINATOR)

            case SwarmState.COORDINATOR:
                result = await self._run_coordinator(context)
                if result.done:
                    self._transition(SwarmState.TEARDOWN)
                else:
                    self._transition(SwarmState.AGENTS_RUNNING)

            case SwarmState.AGENTS_RUNNING:
                await self._run_agents(context)
                self._transition(SwarmState.EVALUATING)

            case SwarmState.EVALUATING:
                if self._round >= self.max_rounds:
                    self._transition(SwarmState.TEARDOWN)
                else:
                    self._transition(SwarmState.COORDINATOR)

            case SwarmState.PAUSED:
                return SwarmPaused(workspace=self.workspace)

            case SwarmState.TEARDOWN:
                return self._teardown()
```

Each `_transition(new_state)` call writes to `swarm_state`, making every state change durable. Pause is an external signal that sets `state='paused'` — the loop picks it up on the next iteration.

### Lifecycle Summary

```
1. SETUP
   ├─ Create workspace directory structure
   ├─ Initialize swarm.db with full schema (tasks, artifacts, signals,
   │  swarm_state, agent_state)
   ├─ Register SwarmDB tool for agent use
   ├─ Inject swarm context: {swarm_db}, {workspace}, {agent_name}, {goal}
   └─ Transition → COORDINATOR

2. COORDINATOR (round N of max_rounds)
   ├─ Run coordinator agent
   ├─ Coordinator populates/updates tasks table, assigns to agents
   ├─ If coordinator signals done → transition → TEARDOWN
   └─ Otherwise → transition → AGENTS_RUNNING

3. AGENTS_RUNNING
   ├─ Launch all agents concurrently (asyncio.TaskGroup)
   ├─ Each agent runs with its prompt + swarm context + SwarmDB tool
   ├─ Agents query tasks table, work, register artifacts, send signals
   ├─ Agent exits update agent_state (done | failed)
   ├─ If paused signal received → transition → PAUSED
   └─ All agents exit → transition → EVALUATING

4. EVALUATING
   ├─ Increment round counter
   ├─ If round >= max_rounds or timeout reached → transition → TEARDOWN
   └─ Otherwise → transition → COORDINATOR (coordinator re-evaluates)

5. PAUSED (entered via external signal)
   ├─ Store paused_from state for resume
   ├─ Return SwarmPaused to caller
   ├─ On resume → transition → paused_from state
   └─ On abort → transition → TEARDOWN

6. TEARDOWN
   ├─ Collect final state from tasks + artifacts tables
   ├─ Build step result: {success, message, tasks, artifacts}
   └─ Clean up workspace per cleanup policy (on_success | always | never)
```

### Agent System Prompt Injection

Each agent receives a composite system prompt:

```
{BASE_SYSTEM_PROMPT}

## Swarm Context
You are agent "{agent_name}" in a swarm working toward: {goal}

### Coordination Database
Use the SwarmDB tool to query and update the tasks, artifacts, and signals tables.

### Workspace
Shared files: {workspace}/shared/
Your scratch space: {workspace}/agents/{agent_name}/

### Other Agents in This Swarm
- backend: <first line of prompt or role summary>
- frontend: ...
- tests: ...

### Conventions
- Claim a task: UPDATE tasks SET status='in_progress', assigned_to='{agent_name}' WHERE id=<id> AND status='pending'
- Complete a task: UPDATE tasks SET status='done', result='<summary>' WHERE id=<id>
- Register an artifact: INSERT INTO artifacts (task_id, agent, name, path) VALUES (...)
- Send a signal: INSERT INTO signals (from_agent, to_agent, type, payload) VALUES (...)
- Check for signals: SELECT * FROM signals WHERE to_agent='{agent_name}' OR to_agent IS NULL

When your assigned work is complete and you have no pending tasks, respond with your final status.
```

### Result Structure

```python
{
    "success": bool,          # True if all tasks reached "done" status
    "message": [str],         # Summary messages from each agent
    "tasks": [                # Final state of all tasks
        {"id": 1, "title": "...", "status": "done", "assigned_to": "backend", ...}
    ],
    "artifacts": [            # All registered artifacts
        {"name": "api_schema", "agent": "backend", "path": "shared/api.json", ...}
    ]
}
```

Results are stored in `results[step_id]` as usual, accessible to subsequent steps via conditions and Jinja2 expressions.

---

## Integration with Existing Systems

| System | Integration |
|---|---|
| **Hooks** | Swarm step supports `hooks` at the step level (applied to all agents) and per-agent overrides. Same override semantics as today. |
| **MCP Servers** | Merged from defaults → step → per-agent, same collision rules. |
| **Conditions** | `condition` on swarm steps works identically — evaluated before the swarm runs. |
| **Foreach** | Supported — runs the entire swarm N times. Each iteration gets its own workspace. |
| **on_failure** | Applied at the swarm step level. Individual agent failures are surfaced through the tasks table; the swarm step fails if the overall goal is not met. |
| **Context/vars** | All workflow vars and context available for `{variable}` interpolation in swarm prompts, workspace path, etc. |
| **`load_project`** | If a prior step used `load_project`, the swarm inherits `project_path`, `project_refs`, `project_agents`, and `project_settings_path`. |

---

## Implementation Plan (High-Level)

1. **Schema**: Add `swarm` to `spec/v1.json` step `oneOf`
2. **SwarmDB tool**: Built-in tool wrapping SQLite operations with validation
3. **SQLite setup**: Workspace creation + `swarm.db` initialization (all 5 tables: `tasks`, `artifacts`, `signals`, `swarm_state`, `agent_state`)
4. **State machine**: `SwarmState` enum, `_get_state()` / `_transition()` methods reading/writing `swarm_state` table
5. **Step class**: `SwarmStep` in `goblin/step.py` implementing `StepProtocol` with state machine orchestrator loop
6. **Agent launcher**: Concurrent agent execution via `asyncio.TaskGroup`, updating `agent_state` on entry/exit
7. **System prompt builder**: Compose swarm-aware prompts per agent (goal, DB conventions, workspace paths, peer agent summaries)
8. **Pause/resume**: External signal mechanism to write `paused` state, `SwarmPaused` return type, resume-from-DB logic
9. **Result collector**: Query final DB state, build result dict
10. **Workspace cleanup**: Apply cleanup policy based on `cleanup` field after teardown
11. **Blueprint integration**: Update `_parse_step()` dispatch in `blueprint.py`
12. **Tests**: Following existing patterns — `_make_blueprint` for parsing, mock steps for orchestration, state transition tests
13. **Docs**: Update CLAUDE.md tables, add example workflow

---

## Resolved Decisions

1. **SQLite access mechanism**: Built-in `SwarmDB` tool inside Goblin. Not an MCP server — avoids adding context overhead to running agents. Goblin registers the tool on each agent, giving it the ability to intercept, validate, and log all DB operations.

2. **Coordinator is mandatory**: The coordinator is the single authority on task decomposition and completion determination. This eliminates the ambiguous "when are we done?" problem. Even simple swarms benefit from a lightweight coordinator that creates obvious tasks and confirms completion after one round.

3. **Coordinator rounds are configurable**: `max_rounds` field with a default of 5. After each agent phase, the coordinator re-evaluates. If it creates new tasks, another agent phase begins. If it signals done or `max_rounds` is exhausted, the swarm proceeds to teardown.

4. **Tasks always come from the coordinator**: No pre-seeded tasks in YAML. Blueprint authors steer task decomposition via the coordinator's prompt. This keeps a single source of truth for task creation and avoids split-brain between static YAML tasks and dynamic coordinator tasks.

5. **SQL signalling is sufficient for v1**: Agents coordinate through the `signals` table — no direct agent-to-agent delegation. All communication is observable in the database, making swarms debuggable after the fact. Direct delegation (nested sub-agents) creates opaque call stacks and complicates resource accounting. May be revisited in a future version if real limitations emerge.

6. **Workspace cleanup is configurable**: `cleanup` field with three options: `on_success` (default — delete workspace if swarm succeeds), `always` (delete regardless of outcome), `never` (preserve for inspection). Applied during teardown.
