# Swarm Step Implementation Plan

This plan implements the `swarm` step type as described in `swarm_prd.md`. Each step is self-contained and ordered by dependency — an agent should execute them sequentially.

---

## Step 1: Add enums module

**File:** `goblin/enums.py` (new)

Create a new module with four `StrEnum` classes as defined in the PRD:

- `SwarmState` — `setup`, `coordinator`, `agents_running`, `evaluating`, `paused`, `teardown`
- `TaskStatus` — `pending`, `in_progress`, `done`, `failed`
- `AgentStatus` — `pending`, `running`, `done`, `failed`, `interrupted`
- `CleanupPolicy` — `on_success`, `always`, `never`

These are the single source of truth for all valid status/state values.

---

## Step 2: Add the SwarmDB tool

**File:** `goblin/swarm_db.py` (new)

Create a class `SwarmDB` that wraps SQLite operations for agents:

1. **`__init__(self, db_path: str)`** — Opens a SQLite connection with WAL mode for concurrent reads. Stores the path.

2. **`init_schema(self)`** — Creates all 5 tables (`tasks`, `artifacts`, `signals`, `swarm_state`, `agent_state`) using the exact DDL from the PRD. Inserts the initial `swarm_state` singleton row with `state='setup'`, `round=0`.

3. **`query(self, sql: str) -> list[dict]`** — Executes a read-only SQL query. Returns list of row dicts. Validates the statement starts with `SELECT`.

4. **`execute(self, sql: str) -> int`** — Executes a write SQL statement (INSERT/UPDATE/DELETE). Validates:
   - Statement is INSERT, UPDATE, or DELETE (not CREATE, DROP, ALTER, etc.)
   - Target table is one of `tasks`, `artifacts`, `signals` (agents can't write to `swarm_state` or `agent_state` directly)
   - Returns `cursor.rowcount`

5. **`get_state(self) -> SwarmState`** — Reads the current swarm state from the `swarm_state` table.

6. **`transition(self, new_state: SwarmState)`** — Updates the `swarm_state` row. Also updates `updated_at`.

7. **`increment_round(self) -> int`** — Increments the `round` column in `swarm_state` and returns the new value.

8. **`update_agent_status(self, name: str, status: AgentStatus, iterations: int | None = None)`** — Updates the `agent_state` table for a named agent.

9. **`get_tasks(self) -> list[dict]`** — Returns all rows from `tasks` table.

10. **`get_artifacts(self) -> list[dict]`** — Returns all rows from `artifacts` table.

11. **`close(self)`** — Closes the SQLite connection.

The `query` and `execute` methods are what agents call via the SwarmDB tool. The other methods are for the orchestrator.

---

## Step 3: Add swarm system prompt

**File:** `goblin/prompts.py` (edit)

Add a new prompt template constant `SWARM_AGENT_PROMPT`:

```
## Swarm Context
You are agent "{agent_name}" in a swarm working toward: {goal}

### Coordination Database
Use the SwarmDB tool to query and update the tasks, artifacts, and signals tables.

Schema:
- tasks (id, title, description, assigned_to, status, result, created_at, updated_at)
- artifacts (id, task_id, agent, name, path, content, created_at)
- signals (id, from_agent, to_agent, type, payload, created_at)

### Workspace
Shared files: {workspace}/shared/
Your scratch space: {workspace}/agents/{agent_name}/

### Other Agents in This Swarm
{agent_roster}

### Conventions
- Claim a task: UPDATE tasks SET status='in_progress', assigned_to='{agent_name}' WHERE id=<id> AND status='pending'
- Complete a task: UPDATE tasks SET status='done', result='<summary>' WHERE id=<id>
- Register an artifact: INSERT INTO artifacts (task_id, agent, name, path) VALUES (...)
- Send a signal: INSERT INTO signals (from_agent, to_agent, type, payload) VALUES (...)
- Check for signals: SELECT * FROM signals WHERE to_agent='{agent_name}' OR to_agent IS NULL

When your assigned work is complete and you have no pending tasks, respond with your final status.
```

Also add a `SWARM_COORDINATOR_PROMPT` template for the coordinator, emphasizing its role in task decomposition and completion evaluation:

```
## Swarm Coordinator
You are the coordinator for a swarm working toward: {goal}

Your job is to:
1. Break the goal into discrete tasks
2. Assign tasks to available agents by writing to the tasks table
3. After agents complete their work, evaluate whether the goal is met

### Available Agents
{agent_roster}

### Coordination Database
Use the SwarmDB tool to manage tasks, artifacts, and signals.

Schema:
- tasks (id, title, description, assigned_to, status, result, created_at, updated_at)
- artifacts (id, task_id, agent, name, path, content, created_at)
- signals (id, from_agent, to_agent, type, payload, created_at)

### Conventions
- Create a task: INSERT INTO tasks (title, description, assigned_to, status) VALUES ('...', '...', '<agent_name>', 'pending')
- Check progress: SELECT * FROM tasks
- Check artifacts: SELECT * FROM artifacts

When all tasks are complete and the goal is met, respond with:
```json
{"done": true, "message": ["<summary>"]}
```

If more work is needed, respond with:
```json
{"done": false, "message": ["<what still needs to happen>"]}
```
```

---

## Step 4: Create SwarmStep class

**File:** `goblin/swarm.py` (new)

This is the largest piece — the `SwarmStep` class implementing `StepProtocol`. It contains the state machine orchestrator.

### 4a: Class skeleton and `__init__`

```python
class SwarmStep(StepProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id = raw["id"]
        self.condition = raw.get("condition")
        self.foreach = raw.get("foreach")
        self.on_failure = raw.get("on_failure")

        swarm = raw["swarm"]
        self.goal = swarm["goal"]
        self.workspace_template = swarm.get("workspace", ".goblin/swarms/{step_id}")
        self.coordinator_config = swarm["coordinator"]
        self.agents_config = swarm["agents"]
        self.max_rounds = swarm.get("max_rounds", 5)
        self.max_iterations = swarm.get("max_iterations", 100)
        self.timeout = swarm.get("timeout")
        self.cleanup = CleanupPolicy(swarm.get("cleanup", "on_success"))
        self.hooks_config = raw.get("hooks", {})
        self.mcp_servers_config = raw.get("mcp_servers")

        self._blueprint = blueprint
        self._db: SwarmDB | None = None
        self._workspace: str = ""
```

### 4b: `_setup(context)` method

1. Resolve the workspace path — interpolate `{step_id}` and any context variables via `.format(**{**context, "step_id": self.id})`.
2. If the workspace path is relative, resolve it relative to `blueprint.project_path` or cwd.
3. Create the workspace directory structure: `workspace/`, `workspace/shared/`, and `workspace/agents/{name}/` for each agent.
4. Instantiate `SwarmDB(workspace/swarm.db)` and call `init_schema()`.
5. Insert a row into `agent_state` for each configured agent with `status='pending'`.
6. Transition state to `COORDINATOR`.

### 4c: `_build_agent_roster()` method

Iterate `self.agents_config`, build a formatted string listing each agent name and the first line of its prompt. Used in system prompt injection.

### 4d: `_run_coordinator(context) -> dict` method

1. Build the coordinator system prompt using `SWARM_COORDINATOR_PROMPT` template — inject `goal`, `agent_roster`, and swarm DB conventions.
2. Prepend `BASE_SYSTEM_PROMPT`.
3. If `blueprint.project_refs` exists, append it.
4. Interpolate the coordinator's `prompt` with context.
5. Call `claude_agent_sdk.query()` with:
   - The composed system prompt
   - `allowed_tools` from `coordinator_config` merged with `["SwarmDB"]` (SwarmDB is always included)
   - Hooks from `_blueprint._build_hooks(self)` — works because `SwarmStep` has `hooks_config`
   - `env={"CLAUDECODE": ""}`
   - `cwd` from blueprint if set
   - Register the SwarmDB tool via the SDK's tool registration mechanism (inspect `ClaudeAgentOptions` for `tools` or `custom_tools` param)
6. Parse the coordinator's final response using `_extract_json` from `step.py`. Look for `{"done": true/false, ...}`. If parsing needs differ from `PromptStep`, add a separate coordinator-specific parser to avoid breaking existing behavior.
7. Return the parsed result.

**SwarmDB tool registration:** The SDK supports `allowed_tools`. The implementing agent must inspect `ClaudeAgentOptions` to find the exact mechanism for registering a callable custom tool (likely `tools` or `custom_tools`). If the SDK doesn't support inline custom tools, the fallback is a Bash-based CLI wrapper script placed in the workspace that agents invoke via `Bash`.

### 4e: `_run_agents(context)` method

1. Launch all agents concurrently using `asyncio.TaskGroup`.
2. For each agent in `self.agents_config`:
   a. Update `agent_state` to `running`.
   b. Build agent system prompt: `BASE_SYSTEM_PROMPT` + `SWARM_AGENT_PROMPT` template (inject `agent_name`, `goal`, `workspace`, `agent_roster`).
   c. If `blueprint.project_refs`, append it.
   d. Interpolate the agent's `prompt` with context.
   e. Call `claude_agent_sdk.query()` with:
      - The composed system prompt
      - `allowed_tools` from agent config merged with `["SwarmDB"]` (SwarmDB always included)
      - Per-agent hooks: if agent config has `hooks`, temporarily set `self.hooks_config` to the agent's hooks and call `_blueprint._build_hooks(self)`, then restore. Otherwise use the step-level hooks.
      - Per-agent MCP servers (merge defaults → step → per-agent)
      - `max_turns` set to `self.max_iterations`
      - `env={"CLAUDECODE": ""}`
      - `cwd` from blueprint if set
   f. On completion, update `agent_state` to `done` with iteration count.
   g. **On exception:** catch the exception inside the per-agent coroutine, update `agent_state` to `failed`, and do **not** re-raise. This prevents one agent's failure from cancelling sibling agents in the `TaskGroup`. (Retry logic will be added in a future version.)
3. Collect text responses from all agents.

### 4f: `_teardown() -> dict` method

1. Query final task state from DB via `db.get_tasks()`.
2. Query all artifacts via `db.get_artifacts()`.
3. Determine success: `True` if all tasks have `status='done'`.
4. Build result dict: `{"success": bool, "message": [...], "tasks": [...], "artifacts": [...]}`.
5. Close the DB connection.
6. Apply cleanup policy:
   - `on_success`: delete workspace dir if `success` is True
   - `always`: delete workspace dir
   - `never`: leave workspace dir
7. Return the result dict.

### 4g: `async run(context) -> dict` — the orchestrator loop

```python
async def run(self, context):
    self._setup(context)

    if self.timeout:
        deadline = asyncio.get_event_loop().time() + self.timeout

    while True:
        state = self._db.get_state()

        if self.timeout and asyncio.get_event_loop().time() > deadline:
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

---

## Step 5: Update `_parse_step` in `blueprint.py`

**File:** `goblin/blueprint.py` (edit)

1. Add import: `from goblin.swarm import SwarmStep`
2. Add a new case to the `match` in `_parse_step`:

```python
case {"swarm": dict(), **_rest}:
    return SwarmStep(raw, self)
```

3. Update the error message in the fallback case to mention `'swarm'` as a third option.

---

## Step 6: Update JSON schema

**File:** `spec/v1.json` (edit)

### 6a: Add `swarm` to the step `oneOf`

Change the step `oneOf` from:
```json
[{"required": ["prompt"]}, {"required": ["load_project"]}]
```
to:
```json
[{"required": ["prompt"]}, {"required": ["load_project"]}, {"required": ["swarm"]}]
```

### 6b: Add `swarm` property to step properties

Add the `swarm` property to the step object:

```json
"swarm": { "$ref": "#/definitions/swarm" }
```

### 6c: Add `swarm` definition

Add a new `definitions.swarm` object:

```json
"swarm": {
  "type": "object",
  "required": ["goal", "coordinator", "agents"],
  "additionalProperties": false,
  "description": "Launches a team of concurrent agents coordinating through a shared SQLite database and filesystem workspace.",
  "properties": {
    "goal": {
      "type": "string",
      "description": "High-level objective injected into every agent's system prompt",
      "examples": ["Implement user authentication with login, signup, and password reset"]
    },
    "workspace": {
      "type": "string",
      "description": "Path for shared files and SQLite DB. Supports {variable} interpolation. Default: .goblin/swarms/{step_id}",
      "examples": [".goblin/swarms/{step_id}", "/tmp/swarm-workspace"]
    },
    "coordinator": { "$ref": "#/definitions/swarmCoordinator" },
    "agents": {
      "type": "array",
      "description": "List of worker agent definitions",
      "minItems": 1,
      "items": { "$ref": "#/definitions/swarmAgent" }
    },
    "max_rounds": {
      "type": "integer",
      "minimum": 1,
      "description": "Maximum coordinator evaluation rounds. Default: 5",
      "examples": [5, 10]
    },
    "max_iterations": {
      "type": "integer",
      "minimum": 1,
      "description": "Maximum turns per agent per round. Default: 100",
      "examples": [50, 100]
    },
    "timeout": {
      "type": "integer",
      "minimum": 1,
      "description": "Swarm-level timeout in seconds. No default (runs until completion or max_rounds).",
      "examples": [600, 1800]
    },
    "cleanup": {
      "type": "string",
      "enum": ["on_success", "always", "never"],
      "description": "Workspace cleanup policy after teardown. Default: on_success",
      "examples": ["on_success", "never"]
    }
  }
}
```

### 6d: Add `swarmCoordinator` definition

```json
"swarmCoordinator": {
  "type": "object",
  "required": ["prompt"],
  "additionalProperties": false,
  "description": "The coordinator agent — decomposes the goal into tasks, assigns them to agents, and evaluates completion",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "Coordinator instruction prompt. Supports {variable} interpolation.",
      "examples": ["Break down the goal into tasks and assign to agents."]
    },
    "allowed_tools": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Tool whitelist for the coordinator agent",
      "examples": [["Read", "Write"]]
    }
  }
}
```

### 6e: Add `swarmAgent` definition

```json
"swarmAgent": {
  "type": "object",
  "required": ["name", "prompt"],
  "additionalProperties": false,
  "description": "A worker agent in the swarm",
  "properties": {
    "name": {
      "type": "string",
      "description": "Unique agent identifier within the swarm",
      "examples": ["backend", "frontend", "tests"]
    },
    "prompt": {
      "type": "string",
      "description": "Agent instruction prompt. Supports {variable} interpolation.",
      "examples": ["You are a backend engineer. Implement assigned tasks."]
    },
    "allowed_tools": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Tool whitelist for this agent",
      "examples": [["Bash", "Read", "Write", "Edit"]]
    },
    "hooks": { "$ref": "#/definitions/hooks" },
    "mcp_servers": { "$ref": "#/definitions/mcpServers" }
  }
}
```

---

## Step 7: Write tests

**File:** `tests/test_swarm.py` (new)

Following the existing test patterns (no SDK calls, mock at step boundary):

### 7a: Test SwarmDB in isolation

- `test_init_schema` — verify all 5 tables exist after `init_schema()`
- `test_query_returns_dicts` — insert a task, query it, verify dict structure
- `test_execute_validates_table` — attempt to write to `swarm_state` via `execute()`, expect rejection
- `test_execute_validates_statement_type` — attempt `DROP TABLE`, expect rejection
- `test_get_state` / `test_transition` — verify state machine reads/writes
- `test_increment_round` — verify round counter increments
- `test_update_agent_status` — insert agent, update status, verify

### 7b: Test SwarmStep parsing

- `test_parse_swarm_step` — use `_make_blueprint` with a swarm step raw dict, verify `SwarmStep` is returned with correct attributes
- `test_parse_step_rejects_swarm_plus_prompt` — step with both `swarm` and `prompt` should fail schema validation (handled by `oneOf`)
- `test_swarm_defaults` — verify `max_rounds=5`, `max_iterations=100`, `cleanup=on_success` when not specified

### 7c: Test SwarmStep._setup

- `test_setup_creates_workspace` — call `_setup`, verify directory structure exists (`shared/`, `agents/{name}/`)
- `test_setup_creates_db` — verify `swarm.db` exists and has the expected tables
- `test_setup_workspace_interpolation` — verify `{step_id}` and context vars are resolved in workspace path

### 7d: Test state machine transitions

- `test_state_transitions` — verify the expected state flow: `setup → coordinator → agents_running → evaluating → coordinator → ... → teardown`
- `test_max_rounds_triggers_teardown` — after `max_rounds` evaluating states, verify transition to teardown
- `test_timeout_triggers_teardown` — verify timeout enforcement

### 7e: Test teardown and cleanup

- `test_teardown_collects_results` — populate tasks/artifacts in DB, verify `_teardown()` returns correct structure
- `test_cleanup_on_success` — workspace deleted when success=True and cleanup=on_success
- `test_cleanup_never` — workspace preserved when cleanup=never
- `test_cleanup_always` — workspace deleted regardless of outcome

### 7f: Test orchestration with mock agents

Using `_make_blueprint_with_mock_steps` pattern — build a blueprint with a swarm step, then mock the agent SDK calls:

- `test_swarm_step_in_blueprint_run` — verify `bp.run()` invokes the swarm step and returns its result
- `test_swarm_step_with_condition` — swarm step with a condition that evaluates false is skipped
- `test_swarm_step_with_foreach` — swarm step with `foreach: 2` runs twice with separate workspaces

---

## Step 8: Add an example workflow

**File:** `workflows/swarm_example.yaml` (new)

Create a minimal but illustrative swarm workflow that exercises the key features:

```yaml
version: v1
workflow:
  name: swarm_demo
  steps:
    - id: build_feature
      swarm:
        goal: "Create a simple Python calculator with add, subtract, multiply, divide and tests"
        coordinator:
          prompt: |
            Break down the goal into tasks. Assign implementation to the coder
            agent and test writing to the tester agent. Monitor progress and
            mark the swarm complete when all tasks pass.
          allowed_tools: [Read]
        agents:
          - name: coder
            prompt: |
              You are a Python developer. Check the tasks table for your
              assignments. Implement each task and write files to the shared
              workspace. Update task status when done.
            allowed_tools: [Bash, Read, Write, Edit]
          - name: tester
            prompt: |
              You are a QA engineer. Watch for completed implementation tasks
              and write pytest tests. Register test results as artifacts.
            allowed_tools: [Bash, Read, Write, Edit]
        max_rounds: 3
        cleanup: never
      on_failure:
        action: abort
```

---

## Step 9: Update CLAUDE.md

**File:** `CLAUDE.md` (edit)

1. Add `goblin/swarm.py` and `goblin/swarm_db.py` and `goblin/enums.py` to the project layout.
2. Add `SwarmStep` to the step types description.
3. Add `swarm` to the key blueprint fields table with status "Implemented".
4. Document the SwarmDB coordination layer briefly.
5. Add `test_swarm.py` to the test files list.

---

## Dependency Graph

```
Step 1 (enums)
  └──→ Step 2 (SwarmDB) — uses SwarmState, TaskStatus, AgentStatus
         └──→ Step 4 (SwarmStep) — uses SwarmDB
Step 3 (prompts) ──→ Step 4 (SwarmStep) — uses prompt templates
Step 4 (SwarmStep) ──→ Step 5 (blueprint.py integration)
Step 6 (schema) — independent, can be done in parallel with steps 1-5
Step 7 (tests) — depends on steps 1-5
Step 8 (example workflow) — depends on step 6
Step 9 (CLAUDE.md) — last, after everything is implemented
```

---

## Resolved Implementation Decisions

1. **SwarmDB tool registration:** The Claude Agent SDK supports `allowed_tools` for specifying which tools an agent can use. `SwarmDB` is a **default tool** for all swarm agents — always included. Each agent's explicitly configured `allowed_tools` are **merged with** `["SwarmDB"]`. The implementing agent must inspect `ClaudeAgentOptions` to determine the exact mechanism for registering SwarmDB as a callable tool (likely `tools` or `custom_tools` param). If the SDK doesn't support inline custom tools, the fallback is a Bash-based CLI wrapper script in the workspace.

2. **Coordinator response parsing:** Reuse `_extract_json` from `step.py` to parse the coordinator's `{"done": true/false, ...}` response. No Pydantic model needed — the shape is simple. **Caution:** if changes to `_extract_json` are needed for the coordinator's response format, ensure they don't break `PromptStep`'s existing parsing. Prefer adding a separate coordinator-specific parser if the shapes diverge.

3. **Hook passing to swarm agents:** `SwarmStep` already stores `hooks_config`. Pass `self` directly to `_build_hooks()` — the method only reads `step.hooks_config`, so it works as-is with any object that has that attribute. No changes to `_build_hooks` signature needed. For per-agent hook overrides, temporarily swap `self.hooks_config` before calling `_build_hooks`, or build hooks manually using the same logic.

4. **Concurrent agent error isolation:** Catch exceptions per-agent inside the `asyncio.TaskGroup` coroutine wrapper and record them in `agent_state` as `failed`. Do **not** let agent exceptions propagate to the task group (which would cancel sibling agents). This is the default behavior. Retry logic based on `agent_state` failures will be added in a future version.
