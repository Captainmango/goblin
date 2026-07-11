# Testing Approach

Tests must never call the OpenHands SDK directly. All tests exercise Goblin's own logic (blueprint parsing, foreach resolution, condition evaluation, hook wiring, etc.) in isolation from the SDK.

## Two helper patterns

1. **`_make_blueprint`** — Construct a `V1Blueprint` from a minimal raw spec dict. This exercises real blueprint parsing and internal methods (`_resolve`, `_resolve_foreach_count`, `_jinja_env`, `_evaluate_condition`) without touching the SDK.

2. **`_make_blueprint_with_mock_steps`** — Build a blueprint the same way, then replace `bp.steps` with `AsyncMock` objects whose `.run()` can be controlled with `return_value` / `side_effect`. This lets `bp.run()` orchestrate steps (foreach iteration, failure handling, context merging) while the step execution itself is a mock — no agent process is started.

## Rules

- **Unit-test blueprint internals directly.** Methods like `_resolve`, `_resolve_foreach_count`, `_jinja_env`, and `_evaluate_condition` are pure logic — test them by calling them on a real `V1Blueprint` instance built via `_make_blueprint`.
- **Mock at the step boundary, not the SDK boundary.** For integration-level tests of `bp.run()`, swap `bp.steps` with `AsyncMock` step objects. Assert on call counts, `call_args_list`, and the returned results dict. Do not mock or patch SDK internals (`openhands`, `AgentDefinition`, `HookMatcher`, etc.).
- **Use `tmp_path` for file-based tests.** When testing file-mode foreach or any file I/O, use pytest's `tmp_path` fixture.
- **Keep tests focused.** Each test class covers one method or feature. Each test method asserts one behavior.