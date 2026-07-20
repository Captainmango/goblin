# Built-in Tools

Goblin provides several built-in tools backed by the OpenHands SDK and custom implementations.

---

## Bash (Terminal)

**Blueprint name:** `Bash` (alias: `terminal`)

Executes shell commands. This is the most powerful tool — use it sparingly and only when necessary.

```yaml
allowed_tools: [Bash]
```

The agent can run arbitrary shell commands in the workspace directory.

---

## Read / Write / Edit / MultiEdit (FileEditor)

**Blueprint names:** `Read`, `Write`, `Edit`, `MultiEdit`
**Underlying tool:** `file_editor`

All four names map to the same OpenHands `FileEditorTool`. The agent uses different sub-commands depending on the operation:

- `Read` — Read file contents.
- `Write` — Write new files.
- `Edit` — Edit existing files.
- `MultiEdit` — Batch multiple edits.

```yaml
allowed_tools: [Read, Write, Edit]
```

Because all names map to `file_editor`, they are deduplicated to a single tool registration.

---

## Glob

**Blueprint name:** `Glob`

Find files matching a glob pattern.

```yaml
allowed_tools: [Glob]
```

Example agent usage: "Find all Python test files with `tests/**/*.py`"

---

## Grep

**Blueprint name:** `Grep`

Search file contents for a pattern.

```yaml
allowed_tools: [Grep]
```

Example agent usage: "Search for all occurrences of `TODO` in the codebase"

---

## Worktree

**Blueprint name:** `Worktree`

Manage git worktrees for isolated working copies. Requires `worktree` configuration.

```yaml
allowed_tools: [Bash, Worktree]
worktree:
  base_dir: .goblin/worktrees
  deps: [node_modules, .venv]
  auto_link: true
```

### Supported Commands

- `list` — Show all registered worktrees.
- `create` — Add a new worktree for a ref (branch, tag, or commit).
- `remove` — Delete a worktree.
- `prune` — Clean up stale worktree entries.
- `link_deps` — Symlink shared dependencies into a worktree.

### Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `base_dir` | `.goblin/worktrees` | Directory for managed worktrees. |
| `deps` | `[]` | Dependency directories to symlink. |
| `auto_link` | `false` | Automatically symlink deps on create. |

---

## AskUser

**Blueprint name:** `AskUser`

Pause execution to ask a human operator a question and block for a reply. Requires `notifications` configuration or falls back to the `default` label.

```yaml
allowed_tools: [AskUser]
notifications:
  label: ops-oncall
```

### Behaviour

- Sends the question via the notifications relay (e.g., Telegram).
- Blocks for up to 15 minutes.
- If no reply arrives, returns an error.

See the [Notifications](../notifications/overview.md) section for setup instructions.
