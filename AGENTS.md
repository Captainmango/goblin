Read CLAUDE.md for guidance.

This is a uv workspace monorepo with three projects: goblin/, goblin-tools/, and notifications-relay/.
Each project has its own pyproject.toml, Taskfile.yml, source package, and tests.
Run tasks from the repo root using the namespaced tasks in the root Taskfile.yml (e.g. `task goblin:lint`, `task goblin-tools:test`).
