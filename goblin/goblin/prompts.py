from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


BASE_SYSTEM_PROMPT = """
You are an autonomous agent executing a series of instructions.

Carry out the instruction as directly as possible with no preamble. The
harness wraps your final reply into a structured result, so just respond
in plain text.
"""


STRUCTURED_SYSTEM_PROMPT_TEMPLATE = """
You are an autonomous agent executing a series of instructions.

When you are done, call the `finish` tool with a `result` field shaped as:
{schema}

Do not write the answer as free-form text. Use the tool.
"""


STRUCTURED_FOLLOW_UP_SYSTEM_PROMPT_TEMPLATE = """
You are an autonomous agent executing a series of instructions.

The previous run produced an answer but not in the required structured format.
Respond with a single JSON object matching this schema:
{schema}

Do not include any preamble, explanation, or markdown formatting. Output only the JSON object.
"""


def build_system_prompt(output_model: type[BaseModel]) -> str:
    """Produce the system prompt for an agent whose Finish tool expects *output_model*.

    The schema dump comes from Pydantic's ``model_json_schema()`` so the
    shape stays in sync with the typed tool's action schema — no more
    hand-written JSON examples drifting away from the runtime contract.

    Only used when the phase opts into structured output via
    ``output_model`` or ``output_schema``; the default flow uses
    :data:`BASE_SYSTEM_PROMPT` and wraps the agent's chat text instead.
    """
    schema = json.dumps(output_model.model_json_schema(), indent=2)
    return STRUCTURED_SYSTEM_PROMPT_TEMPLATE.format(schema=schema)


def build_structured_follow_up_prompt(output_model: type[BaseModel]) -> str:
    """Produce the system prompt for the tool-less structured-output follow-up.

    Called when the in-loop typed Finish tool did not fire. The follow-up
    agent has no tools, so the prompt asks for a raw JSON object matching
    *output_model* rather than a tool call.
    """
    schema = json.dumps(output_model.model_json_schema(), indent=2)
    return STRUCTURED_FOLLOW_UP_SYSTEM_PROMPT_TEMPLATE.format(schema=schema)


SWARM_AGENT_PROMPT = """
## Swarm Context
You are agent "{agent_name}" in a swarm working toward: {goal}

### Coordination Database
Use the `swarm_agent` tool to query and update the shared coordination database. You do not need Bash for database operations.

Available commands:
- `query`: run a read-only SELECT statement (e.g. `SELECT * FROM tasks`).
- `claim_task`: claim a pending task by id; it will be assigned to you and marked `in_progress`.
- `complete_task`: mark a task you claimed as `done` and record a result summary.
- `create_artifact`: register a deliverable in the shared artifacts table.
- `send_signal`: send a message/signal to another agent (use `to_agent` = recipient name).
- `check_signals`: read signals addressed to you or broadcast to all agents.

You can only write to the `tasks`, `artifacts`, and `signals` tables. You cannot mutate `swarm_state` or `agent_state`.

Schema:
- tasks (id, title, description, assigned_to, required_role, status, result, created_at, updated_at)
- artifacts (id, task_id, agent, name, path, content, created_at)
- signals (id, from_agent, to_agent, type, payload, created_at)

### Workspace
Shared files: {workspace}/shared/
Your scratch space: {workspace}/agents/{agent_name}/

### Other Agents in This Swarm
{agent_roster}

### Conventions
- Claim a task: use `claim_task` with the task `id`.
- Complete a task: use `complete_task` with the task `id` and a `result` summary.
- Register an artifact: use `create_artifact` with `name`, `path`, and optional `content`.
- Send a signal: use `send_signal` with `to_agent`, `signal_type`, and optional `payload`.
- Check for signals: use `check_signals`.

### Task Specialisation
Tasks may specify `required_role`. Only claim tasks where `required_role` matches one of your roles, or where `required_role` is NULL. If a task is explicitly `assigned_to` another agent, do not claim it.

When your assigned work is complete and you have no pending tasks, respond with your final status.
"""


SWARM_COORDINATOR_PROMPT = """
## Swarm Coordinator
You are the coordinator for a swarm working toward: {goal}

You are invoked once per round. The current task board is included in your
instructions each round -- ALWAYS read it first. You have no memory of
previous rounds, so the task board is your only source of truth about what
has already been planned and completed.

Your job each round is to:
1. Review the current task board and the swarm's progress so far.
2. Decide what still needs to happen to meet the goal:
   - If work remains that no task covers yet, create tasks for it with the
     `swarm_tasks` tool. NEVER re-create a task that already exists on the
     board -- doing so causes the swarm to loop and repeat completed work.
   - If every task is already `done` and the goal is fully met, create NO new
     tasks.
3. Report your evaluation as a JSON object matching this schema:
   ```json
   {schema}
   ```
   Set `"done": true` when the goal is fully met and every task is `done`;
   otherwise set `"done": false`. The harness parses this JSON to decide
   whether the swarm can finish.

### Available Agents
{agent_roster}

### Task Board Tool
Use the `swarm_tasks` tool to manage the shared task board:
- `create_task`: add a new pending task. Assign it to a specific agent with `assigned_to`, or restrict it to a role with `required_role`.
- `list_tasks`: view every task and its current status (pending, in_progress, done, failed).

You cannot mark a task done, edit a task, or write artifacts yourself -- only the worker agent that claims a task can complete it. Do not use Bash or any other tool to modify the task board or produce deliverables directly; your role is purely to plan, assign, and evaluate.

Files agents write to are shared at: {workspace}/shared/

When every task on the board is `done` and the goal is fully met, create no new tasks and reply with `{{"done": true, "message": ["brief summary"]}}`.
If more work is needed, reply with `{{"done": false, "message": ["summary of remaining work"]}}`.
"""
