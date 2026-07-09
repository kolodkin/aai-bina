# Git sync: per-entity backup & restore of queries and dashboards

Back up predefined queries and dashboards to a git repository (e.g. GitHub) and
restore them from any point in that repository's history, per entity, from
inside QueryView.

## Goals

- **Store to git**: back up a single predefined query or dashboard as files in
  a git repo, one commit per store, pushed to a configured remote.
- **Restore from git**: overwrite the local DB row for one query or dashboard
  with its content at any past commit.
- **History view**: list an entity's revisions (last 10, paginate indefinitely)
  so the UI and MCP clients can offer "restore with version options".

## Non-goals (v1)

- Connections are **never** written to the repo — their config is encrypted
  credentials. Dashboards reference connections by name only.
- No whole-DB snapshot store/restore; everything is per entity. (Bulk export
  can be added later on top of the same serializers.)
- No auto-backup on save; store is always an explicit user/agent action.
- No merge/conflict UI. The backend serializes git operations and re-syncs with
  the remote before each store.

## Versioning model

Git commits **are** the versions. There is no `version.txt`, no counter, no
per-entity tags.

- Each `store` produces exactly one commit touching exactly one entity's files.
  An entity's version history is therefore `git log -- <its path>`.
- "Version N of query X" = the Nth-oldest commit touching X's path. Friendly
  version numbers are derived, never stored.
- **Restore never moves HEAD.** It reads files at a commit
  (`git show <sha>:<path>`), parses them, and upserts the DB row. No checkout,
  no reset, no history rewrite. Storing after a restore creates a *new* commit
  on top whose content matches an older one — history is append-only.

## Repository layout

Mirrors the DB keying (queries are keyed by connection *type* + name;
dashboards are global by name):

```
queries/
  clickhouse/
    top_errors.yaml        # query, cell_view, order_by, fields
  postgres/
    slow_locks.yaml
dashboards/
  sales_overview/
    meta.yaml              # name, connection
    dashboard.html         # agent-authored HTML, as a real .html file
    queries.yaml           # {query_name: SQL}
```

- **Query file** (`queries/{type}/{slug}.yaml`): keys `query_name`, `query`,
  `cell_view` (raw YAML text stored as a string, verbatim — it is never
  interpreted server-side), `order_by`, `fields` (both stored as parsed YAML
  values, serialized back to the DB's JSON text on restore). Absent/None keys
  are omitted.
- **Dashboard dir** (`dashboards/{slug}/`): `meta.yaml` holds `name` and
  `connection`; `dashboard.html` holds the HTML verbatim (real file → real
  diffs); `queries.yaml` maps query name → SQL (multiline literal block style
  for readable diffs).
- **Slugs**: file/dir names are the entity name with characters outside
  `[A-Za-z0-9._ -]` percent-encoded (and leading dots encoded). The canonical
  name always lives *inside* the YAML (`query_name` / `name`); restore trusts
  the YAML, not the path.

## Backend: `queryview/gitsync.py`

New module owning serializers and git plumbing. New dependency: PyYAML.

### Configuration (env vars)

| Var | Meaning | Default |
| --- | --- | --- |
| `GIT_SYNC_REMOTE` | Remote URL (https with embedded token, or ssh) | — (unset ⇒ git sync disabled) |
| `GIT_SYNC_BRANCH` | Branch to commit/push to | `main` |
| `GIT_SYNC_DIR` | Local clone path | `{db_path}.gitsync/` |

When `GIT_SYNC_REMOTE` is unset, every git-sync endpoint/tool returns a clear
`git sync is not configured` error. Auth is deploy-time config; the app never
handles GitHub credentials beyond passing the remote URL to git.

### Git plumbing

- Shell out to the `git` binary (via `asyncio.create_subprocess_exec`); no
  GitHub API dependency, works with any git host.
- Lazy init: on first operation, clone the remote into `GIT_SYNC_DIR` (or
  `git init` + add remote when the remote repo is empty).
- All operations run under a single `asyncio.Lock` — one git op at a time.
- **Store** re-syncs first: `git fetch` + `git reset --hard origin/{branch}`,
  then re-exports the entity from the DB, commits, pushes. Because export is
  deterministic from the DB and each commit touches one entity, resetting to
  the remote head is always safe and avoids push rejections.
- **Store with no content change** makes no commit and reports "no changes".
- **History/restore** run `git fetch` first so remote-only commits (e.g. pushed
  from another instance) are visible; they read objects directly
  (`git log`, `git show`) without touching the working tree.

### Operations

- `store(kind, name, conn_type=None, message=None)` — export the entity's
  files, `git add` (including deletion of files no longer produced), commit
  (default message `store {kind} {type+name|name}`), push. Returns the new
  commit sha. Errors if the entity doesn't exist in the DB.
- `history(kind, name, conn_type=None, before=None, limit=10)` — commits
  touching the entity's path, newest first. `before=<sha>` pages older
  (`git log <before>^ -- <path>`). Returns
  `[{sha, message, date}]` + `has_more`.
- `restore(kind, name, conn_type=None, ref="HEAD")` — read the entity's files
  at `ref` (`origin/{branch}` when `ref` is HEAD), parse, upsert the DB row via
  the existing `save_predefined_query` / `upsert_dashboard`. Overwrites the
  local row unconditionally. Errors if the entity is absent at that ref.
- A dashboard restored with a `connection` name that doesn't exist locally is
  restored as-is; running it fails until a matching connection exists (same
  behavior as deleting a connection today).

## API surface

REST (mirroring the existing `/api/dashboards` style; thin wrappers in
`main.py`, logic in `gitsync.py`):

- `GET /api/git/status` → `{configured}` — lets the UI disable Commit/Restore
  with a tooltip when `GIT_SYNC_REMOTE` is unset.
- `POST /api/git/store` — body `{kind, name, conn_type?, message?}` →
  `{sha}`.
- `GET /api/git/history?kind=&name=&conn_type=&before=&limit=10` →
  `{revisions: [{sha, message, date}], has_more}`.
- `POST /api/git/restore` — body `{kind, name, conn_type?, ref?}` → `{ok}`.

`kind` is `"query"` or `"dashboard"`; `conn_type` is required for queries,
ignored for dashboards. Not-configured / entity-not-found / git failures map to
409 / 404 / 502 with a message.

MCP tools (in `mcp_server.py`, same trio): `git_store`, `git_history`,
`git_restore` — same parameters and results, so agents get the identical
store/restore-with-versions flow.

## Frontend UX

Per-entity controls, next to where each entity already lives — three distinct
buttons:

- **Save** — the existing action, unchanged: upsert to the local DB. Never
  touches git.
- **Commit** — export the entity's *saved* DB state to the repo (one commit,
  push), via `POST /api/git/store`. Unsaved panel edits are not committed —
  save first, then commit.
- **Restore** — opens the revision picker for that entity: a scrollable list
  of its revisions, newest first, 10 fetched at a time, fetching the next page
  indefinitely as the user scrolls (using `before=<oldest loaded sha>`). Each
  row: short sha, date, commit message, and a **Restore** action. The list is
  fed by `GET /api/git/history`; restoring asks for confirmation ("Overwrite
  local '<name>' with this version?") before calling `POST /api/git/restore`,
  then reloads the entity in place.

Placement: the **query panel** shows the trio alongside the predefined-query
selector (acting on the currently selected predefined query); the **dashboard
page** shows the same trio for the selected dashboard. When git sync is
unconfigured, Commit and Restore render disabled with a tooltip explaining
`GIT_SYNC_REMOTE` must be set (Save is unaffected).

## Error handling

- Git failures (network, auth, push rejection after retry) surface the git
  stderr tail in the API error message.
- Restore parses YAML defensively: malformed files at a ref produce a 502 with
  the parse error; the DB is never partially updated (parse fully, then one
  upsert).
- The re-sync-before-store strategy makes concurrent pushes from two QueryView
  instances last-writer-wins per entity — acceptable for v1 and noted here
  deliberately.

## Testing

- **Serializer round-trip**: query/dashboard → files → DB row equals the
  original (including None fields, unicode names, multiline SQL/HTML).
- **Git flow** against a local bare repo as the remote (no network, no
  GitHub): store creates one commit touching only the entity's path; store
  with no change makes no commit; history paginates with `before`; restore at
  an old sha overwrites the DB row and does not move HEAD.
- **API tests** for the three endpoints incl. the unconfigured error.
- e2e: CI starts a loopback `git daemon` (composite action, with
  `--enable=receive-pack` since store pushes) serving an empty bare repo over
  git:// and exports it as `GIT_SYNC_REMOTE` before starting the backend; the
  test commits and restores a dashboard through the UI. On unconfigured
  stacks the flow test skips and the controls are asserted disabled instead.
