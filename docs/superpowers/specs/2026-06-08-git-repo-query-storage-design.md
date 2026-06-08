# File-backed queries & dashboards in a local git repo

**Status:** design approved, not yet implemented.

## Goal

Move query and dashboard artifacts out of SQLite into a git-versioned file
tree. The motivation is **version history and diffs**: every saved query and
dashboard becomes a file you can diff, blame, and revert through git. Storage
stays local — no git remote / sync is in scope.

## Scope

In scope:

- A single local git repo as the source of truth for predefined queries and
  dashboards, stored as files.
- A commit control in the UI (plus backing endpoints) so changes are committed
  on demand.
- Dropping the now-unused SQLite tables.

Out of scope (unchanged by this work):

- Connections — they stay in SQLite as the source of truth for identity and
  encrypted secrets (see [connect.md](../../connect.md)).
- Git remotes / push / pull / multi-machine sync.
- Sessions, cookies, auto-connect, password encryption, the query panel, the
  dashboard iframe sandbox, `window.queries`, and `cell_view` / `params`
  semantics. The YAML schema is identical, just relocated to files.

## Storage model

### Connections stay in SQLite

The `connections` table is untouched. It remains the source of truth for
connection identity (name, type, host, port, username), the encrypted password,
and the selected database. Queries are still scoped by connection **type**
(e.g. all ClickHouse connections share the same queries), which the file layout
preserves.

### Queries & dashboards move to a global git repo

A single git repo (default `backend/queryview-data/`, overridable with
`DATA_PATH`) holds all query and dashboard artifacts as files:

```
queryview-data/                          (git repo)
├── queries/
│   └── clickhouse/                      ← connection type (keeps type-sharing)
│       └── <query_slug>/
│           ├── query.sql                ← the SQL
│           └── queryview.yaml           ← cell_view + params (today's YAML, verbatim)
└── dashboards/
    └── <dashboard_slug>/
        ├── dashboard.yaml               ← { name, connection, queries: {name: SQL} }
        └── index.html                   ← agent-authored HTML
```

Details:

- **Repo init.** On startup the app ensures `DATA_PATH` is a git repo; if not,
  it runs `git init` there. The directory is gitignored from the project repo so
  the nested repo is not tracked by the outer one.
- **Slugging.** A directory name is a filesystem-safe slug of the query or
  dashboard name. The canonical display name is stored inside the YAML
  (`queryview.yaml` for queries via a reserved key; `name` in `dashboard.yaml`)
  so names with spaces or unusual characters round-trip. Upsert targets the same
  slug, so re-saving a name overwrites its directory.
- **Commit identity.** Commits are made with a fixed app identity
  (`QueryView <queryview@localhost>`) so commits succeed in fresh environments
  with no global git config.

### Clean break on existing data

The `predefined_queries` and `dashboards` SQLite tables are dropped via an
Alembic migration. No existing rows are migrated; the data repo starts empty.

## Read / write behavior

The existing REST and MCP surface is unchanged; only the backing store swaps
from SQLite to the filesystem. **Saves write the working tree only — they never
commit.**

| Surface | Behavior |
| --- | --- |
| `GET /api/predefined-queries?type=<t>` | List by scanning `queries/<t>/*/`; each entry returns `query_name`, `query`, `cell_view` as today. |
| `POST /api/predefined-queries` | Write `query.sql` + `queryview.yaml` under `queries/<type>/<slug>/`. Empty/missing `cell_view` omits/clears the YAML. |
| `GET /api/dashboards` / `GET /api/dashboards?name=` | List / read from `dashboards/`. |
| `POST /api/dashboards` and MCP `upsert_dashboard` | Write `dashboard.yaml` + `index.html`; persist = write working tree (no commit). The push-to-session path is unchanged. |
| `/api/runqueries` | Reads the dashboard's queries from files; runtime behavior unchanged. |

## Commit control (new)

### Endpoints

- **`GET /api/repo/status`** → the list of changed paths in the data repo
  (git status: added / modified / deleted, relative to the last commit).
- **`POST /api/repo/commit`** `{message}` → stage all changes and commit them
  with the given message under the app identity. Returns the new commit's
  short SHA (or an indication there was nothing to commit).

### UI

- Lives in the **top-left control cluster** beside the connection chip and the
  remote-control toggle (the `absolute left-4 top-4` row in `App.tsx`). The
  cluster is lifted so the commit control renders regardless of connection
  state (the data repo is global, independent of any connection).
- A button shows a **pending-change-count badge** when the repo is dirty.
  Clicking it opens a popover (same pattern as the existing agent panel) listing
  the **changed files**, a **message box**, and a **Commit** button. Committing
  clears the list and badge.

## Testing

- **Backend** — the file store (read / write / list, slug round-tripping for odd
  names, overwrite-on-resave) and the `status` / `commit` endpoints, exercised
  against a temporary git repo (`DATA_PATH` pointed at a tmp dir).
- **e2e (Playwright)** — save a predefined query → it appears in the commit
  status panel → commit with a message → the panel clears; the same flow for a
  dashboard.

## Migration / docs follow-up

- Alembic revision dropping `predefined_queries` and `dashboards`.
- Update `docs/query.md`, `docs/dashboard.md`, `docs/connect.md`, and
  `docs/api.md` to describe file-backed storage and the new `/api/repo/*`
  endpoints; remove the dropped-table schemas.
