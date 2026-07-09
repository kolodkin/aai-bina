# Future

Planned work. Each entry is a proposal, not yet implemented — the spec lives
here until it ships, then moves into the relevant doc.

## Edit / delete predefined queries

Predefined queries can currently be saved (which upserts by name) and loaded,
but not renamed or removed from the UI. Add a way to rename and delete saved
queries — likely a `DELETE /api/predefined-queries` endpoint and edit/delete
controls in the query panel's predefined-query selector.

## Per-project git sync (project resolution)

Git sync (see [gitsync.md](./gitsync.md)) backs everything up to a single repo
configured by one global `GIT_SYNC_REMOTE`. Introduce a **project** concept so each project resolves to
its own GitHub repo: queries and dashboards belong to a project, and
commit/restore/history operate against that project's remote.

Sketch, to be designed properly when picked up:

- A `projects` table (name → remote URL, branch), with entities gaining a
  project reference; existing data lands in a "default" project mapped to the
  current `GIT_SYNC_REMOTE`.
- `gitsync.py` resolves remote + clone dir per project instead of from global
  env; one clone per project under `{db_path}.gitsync/{project}/`.
- API/MCP surface grows a `project` parameter (defaulting to "default"), and
  the UI a project switcher.
- Repo layout inside each project's repo stays exactly as today — the project
  is resolved to a remote, not encoded in paths.

## Git sync follow-ups

Small improvements noted in the git-sync final review, none blocking:

- MCP `git_store`/`git_history`/`git_restore` lack the REST layer's up-front
  validation — a typo'd `kind` falls through to dashboard treatment and a
  missing `conn_type` surfaces as a generic 404 instead of a clear message.
- `restore` treats only a missing `meta.yaml` at a ref as 404; missing
  `dashboard.html`/`queries.yaml` silently default (unreachable today because
  store writes all three files in one commit — remove or document the
  asymmetry).
- Restoring by branch *name* resolves the local branch, which fetch doesn't
  fast-forward, so it can read a stale tree; sha/HEAD paths (the documented
  usage) are correct. Resolve named refs against `origin/` or note it in
  [gitsync.md](./gitsync.md).
- No push retry: under the single per-instance lock a store never self-races,
  but a second QueryView instance pushing between our fetch and push rejects
  the store. Fine for v1's single-instance scope; add one retry if that
  changes.

## Related docs

- [api.md](./api.md) — backend JSON API.
- [connect.md](./connect.md) — connecting, storage, sessions.
- [queryview.md](./queryview.md) — the single-prompt page concept.
- [query.md](./query.md) — running queries: pagination, predefined queries, CSV.
