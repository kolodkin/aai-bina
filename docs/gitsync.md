# Git sync — backup & restore

Predefined queries and dashboards can be backed up to a git repository (e.g.
GitHub) and restored from any past revision, per entity. Connections are never
written to the repo (their config is encrypted credentials).

## Configuration

| Env var           | Meaning                                   | Default              |
| ----------------- | ----------------------------------------- | -------------------- |
| `GIT_SYNC_REMOTE` | Remote URL (https with token, or ssh)     | unset ⇒ disabled     |
| `GIT_SYNC_BRANCH` | Branch to commit/push to                  | `main`               |
| `GIT_SYNC_DIR`    | Local clone path                          | `{db_path}.gitsync/` |

## Repository layout

```
queries/{type}/{name}.yaml         # query, cell_view, order_by, fields
dashboards/{name}/meta.yaml        # name, connection
dashboards/{name}/dashboard.html   # the HTML, verbatim
dashboards/{name}/queries.yaml     # {query_name: SQL}
```

File names are percent-encoded where needed; the canonical name lives inside
the YAML.

## Versioning

Git commits are the versions — each Commit makes exactly one commit touching
one entity, so an entity's history is `git log -- <its path>`. Restore reads
files at a chosen commit (`git show`) and overwrites the local DB row; HEAD
never moves and history is append-only.

## UI

Next to each entity's existing **Save** button (query panel and dashboard
page): **Commit** pushes the saved DB state to the remote; **Restore** opens
the entity's revision list (newest first, 10 at a time, scroll for more) and
overwrites the local copy with the picked revision after confirmation. Both
are disabled when `GIT_SYNC_REMOTE` is unset.

## API

- `GET /api/git/status` → `{configured}`
- `POST /api/git/store` `{kind, name, conn_type?, message?}` → `{ok, committed, sha, message}`
- `GET /api/git/history?kind=&name=&conn_type=&before=&limit=10` → `{ok, revisions: [{sha, date, message}], has_more}`
- `POST /api/git/restore` `{kind, name, conn_type?, ref?}` → `{ok, restored, sha}`

`kind` is `"query"` or `"dashboard"`; `conn_type` is required for queries.
MCP tools `git_store`, `git_history`, `git_restore` mirror the same surface.

## Related docs

- [query.md](./query.md) — predefined queries.
- [dashboard.md](./dashboard.md) — dashboards.
- [api.md](./api.md) — backend JSON API.
