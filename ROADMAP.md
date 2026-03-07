# Goblin Roadmap

## Improvements

### Quality

#### Replace `print()` with Structured Logging
The codebase uses `print()` throughout (`blueprint.py`, `swarm.py`, `runner.py`). Switching to Python's `logging` module would let consumers control verbosity, route to files, and silence output in tests without monkeypatching stdout.

#### `WorkflowRunner` Result Output
`WorkflowRunner.run()` calls `print(results)` unconditionally. Results should be returned and logged at a configurable level, not dumped raw to stdout.

#### Retry Count is Not Configurable
`on_failure.action: retry` always retries exactly once. There is no `max_retries` field in the schema or step config. The retry count should be a first-class field.

#### Hook Override vs. Merge
Step-level hooks silently replace workflow defaults rather than extending them. This is intentional but undocumented and surprising. At minimum it should be called out in the schema description. A `hooks.merge` flag that allows additive behaviour would give authors more control.

---

## Enhancements

### Blueprint Schema Validation at Load Time
`WorkflowRunner` currently loads and runs blueprints without validating them against `spec/v1.json`. Adding `jsonschema` validation at load time would surface authoring errors (unknown fields, wrong types, missing required keys) immediately with clear messages instead of cryptic runtime failures.

### Dry-Run Mode
A `--dry-run` CLI flag that parses and validates the blueprint, resolves foreach and condition expressions against an empty results dict, and prints a preview of which steps would execute and in what order — without invoking any agents.

### Structured Step Output Schema
Allow steps to declare an `output_schema` (JSON Schema fragment) that the runner validates the agent's response against. This would make data flow between steps explicit and catch mis-shaped outputs early rather than letting a bad value silently propagate.

### Resume / Checkpointing
Persist step results to a `.goblin/run-<id>.json` checkpoint file after each step completes. A `--resume` flag would reload the checkpoint and skip already-completed steps, allowing interrupted long-running workflows to continue from where they left off.

### Per-Step Model Selection
Allow blueprint authors to specify which Claude model a step runs against (e.g. `model: claude-opus-4-6` for critical steps, a faster model for lightweight ones). The spec and SDK options would need to carry this through.

### Blueprint Composition
A top-level `import` or `include` directive that allows one blueprint to embed another as a sub-workflow. This would encourage reuse of common step sequences (clone repo, load project, run tests) across multiple workflow files without copy-paste.

### Swarm Task Priorities
Add a `priority` column to the `tasks` table in `SwarmDB` and expose it in the `swarmdb` CLI script. The coordinator could assign priorities so high-priority tasks are claimed by agents before lower-priority ones, giving the author more control over execution order within a round.

### Swarm Agent Roles
Allow swarm agents to declare a list of `roles` (e.g. `["backend", "database"]`) and allow tasks to specify a required `role`. The coordinator and agents would use this to assign and filter tasks by specialisation rather than relying entirely on agent names.

### Hook Plugins from External Files
Allow hook names in blueprints to reference hooks defined in external Python files (e.g. `hooks: {post_tool: ["./hooks/run_tests.py::run_tests"]}`). This would make hooks portable alongside workflow files without requiring users to modify the goblin package itself.

### TUI Progress Dashboard
A terminal UI (e.g. using `rich` or `textual`) that renders live step progress, agent turn counts, swarm task states, and hook output during workflow execution. Currently all feedback is interleaved `print()` output — a structured view would make long-running workflows much easier to monitor.
