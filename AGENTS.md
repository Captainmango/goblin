Read CLAUDE.md for guidance.

This is a uv workspace monorepo with two projects: goblin/ and notifications-relay/.
Each project has its own pyproject.toml, Taskfile.yml, source package, and tests.
Run tasks from the repo root using the namespaced tasks in the root Taskfile.yml (e.g. `task goblin:lint`, `task notifications-relay:test`).
