# Workspaces — per-workspace git sync (design)

Replaces the "Per-project git sync (project resolution)" entry in
[future.md](../../future.md). The concept is named **workspace** (not
"project", which reads too much like a GitHub-side notion).

## Goal

Queries and dashboards belong to a **workspace**, and each workspace syncs to
its own git remote. Today git sync (see [gitsync.md](../../gitsync.md)) backs
everything up to one repo from a global `GIT_SYNC_REMOTE`; after this change
the remote, branch, clone, and history are all per-workspace.

## Decisions

- **Workspace is a full ownership dimension**, not just sync routing: every
  query/dashboard belongs to exactly one workspace, entity names are unique
  *per workspace*, the UI filters by an active workspace, and git operations
  derive the remote from the entity's workspace.
- **Config lives in the DB**: a `workspaces` table holds name, remote URL
  (encrypted at rest — https remotes may embed tokens), and branch. CRUD via
  API/UI/MCP; no restart to add a workspace.
- **Connections stay global** — they are server-level credentials, never
  synced to git; any workspace can use any connection. Workspace-scoped
  connections are a future.md follow-up.
- **Requests name the workspace explicitly**: scoped endpoints and MCP tools
  take an optional `workspace` parameter defaulting to `"default"`, so
  existing clients keep working unchanged. No server-side "current workspace"
  state.
- **Delete refuses on non-empty** (409). Since entity deletion doesn't exist
  yet (future.md's "edit/delete predefined queries"), non-empty workspaces are
  effectively undeletable in v1; that entry unlocks it.
- **`"default"` is an ordinary row** — created by the migration, deletable and
  renamable like any other; only the fallback for an omitted `workspace`
  parameter points at it (and errors cleanly if it's gone).
- **Remote is optional** — a workspace can be a pure namespace; its git
  controls are disabled (the existing "not configured" 409, now
  per-workspace).

## Data model

- New `workspaces` table: `id` (PK), `name` (unique), `remote` (nullable,
  AES-256-GCM-encrypted with the existing machinery in `connect.py`),
  `branch` (default `"main"`).
- `predefined_queries` and `dashboards` gain a non-null `workspace_id` FK.
  Unique constraints become `(workspace_id, type, query_name)` and
  `(workspace_id, name)`.
- One alembic migration: create the table; insert the `default` workspace
  (remote = encrypted `GIT_SYNC_REMOTE` if set, branch = `GIT_SYNC_BRANCH` or
  `"main"`); backfill all existing entity rows with its id; tighten the
  unique constraints.
- Connections untouched.

## Backend

- New `workspaces.py`: CRUD plus `resolve(name) -> Workspace` (404 for an
  unknown name; delete returns 409 while the workspace still owns entities).
- `gitsync.py`: `_remote()`/`_branch()`/`_workdir()` are replaced by fields of
  a workspace record passed into `store`/`history`/`restore`/`configured`.
  Each workspace clones to `{db_path}.gitsync/{workspace_id}/` and has its own
  lock (keyed by loop + workspace id), so syncs to different remotes don't
  serialize each other. Clone dirs and FKs are keyed by id, so renaming a
  workspace is a one-row update. Repo layout inside each clone is unchanged.
- A workspace with no remote raises the existing "not configured" 409.
- `GIT_SYNC_REMOTE`/`GIT_SYNC_BRANCH` become seed-only: read once by the
  migration, ignored at runtime. `GIT_SYNC_DIR` remains as the base-directory
  override for the per-workspace clones.

## API / MCP

- New endpoints: `GET/POST/PATCH/DELETE /api/workspaces` (list returns name,
  branch, and whether a remote is configured — never the remote URL's
  credentials).
- Existing scoped endpoints — predefined queries, dashboards, and
  `git/store|history|restore|status` — accept an optional `workspace` (name)
  defaulting to `"default"`. `git/status` reports `{configured}` for the given
  workspace.
- MCP: existing tools gain the optional `workspace` parameter, plus one
  read-only `list_workspaces` tool (names + configured flag) so agents can
  discover valid names. No workspace CRUD via MCP — like connections, it's
  admin configuration involving secrets (the remote URL embeds a token) and
  belongs to the API/UI only.

## Frontend

- Workspace switcher in the app header: a dropdown of workspaces plus a manage
  dialog (create, rename, set/clear remote, delete). The active workspace
  persists in localStorage; every scoped API call passes it; switching
  reloads the query and dashboard lists.
- `GitSyncControls` disabled state becomes per-workspace, driven by the active
  workspace's `configured` status.

## Testing

- Backend: workspace CRUD; per-workspace name uniqueness; migration backfill
  (existing rows land in `default` with the env remote); gitsync against two
  loopback remotes — a commit in workspace A never appears in B; no-remote
  409; delete-non-empty 409.
- e2e: extend the existing loopback-git-daemon test to two workspaces.

## Docs

- New `docs/workspace.md` (doc of record once shipped).
- `gitsync.md`: configuration section rewritten (env vars → workspace
  settings), API section gains the `workspace` parameter.
- `future.md`: this entry removed; new follow-up entry for workspace-scoped
  connections.
