# Swarm Backends

Goblin supports two database backends for swarm state: **Turso/libSQL** (default) and **SQLite** (legacy).

## Turso / libSQL (Default)

Turso is the default backend. It uses libSQL, a fork of SQLite that supports remote replication and local-first operation.

### Local Mode

By default, the swarm creates a local libSQL database at the workspace path:

```yaml
swarm:
  backend: turso
  # No turso block needed — defaults to local mode
```

### Remote Mode

Connect to a cloud Turso database with a local replica:

```yaml
swarm:
  backend: turso
  turso:
    mode: remote
    url: libsql://my-db.turso.io
    auth_token: $TURSO_AUTH_TOKEN
    local_path: .goblin/swarms/{phase_id}/swarm.db
```

### Turso Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `mode` | `local` | `local` or `remote`. |
| `url` | Workspace `swarm.db` | Database URL or local path. |
| `auth_token` | — | Required for remote mode. |
| `local_path` | Workspace `swarm.db` | Local replica path for remote mode. |

### Environment-Based Configuration

You can also configure Turso via environment variables:

```env
GOBLIN_TURSO_URL=libsql://my-db.turso.io
GOBLIN_TURSO_AUTH_TOKEN=...
GOBLIN_TURSO_MODE=remote
GOBLIN_TURSO_LOCAL_PATH=.goblin/swarms/swarm.db
```

## SQLite (Legacy)

Use the legacy SQLite backend by setting:

```yaml
swarm:
  backend: sqlite
```

Or via environment variable:

```env
GOBLIN_BACKEND=sqlite
```

SQLite mode uses the standard `sqlite3` module without libSQL features. It is suitable for simple local swarms but lacks remote replication.
