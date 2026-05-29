# Future

Planned work and roadmap items. Each entry is a proposal, not yet implemented —
the spec lives here until it ships, then moves into the relevant doc.

## Edit / delete predefined queries

Predefined queries can currently be saved (which upserts by name) and loaded,
but not renamed or removed from the UI. Add a way to rename and delete saved
queries — likely a `DELETE /api/predefined-queries` endpoint and edit/delete
controls in the query panel's predefined-query selector.

## Schema migrations via Alembic

`connect._ensure_schema()` runs `SQLModel.metadata.create_all()` on first DB
touch, which creates new tables but does **not** evolve existing ones — adding a
column to a model is invisible to a SQLite file created before the change.

The `cell_view` rollout previously carried a bespoke `ALTER TABLE … ADD COLUMN`
+ an idempotency flag in `queries.py`. That pattern doesn't scale: every new
column would need its own ad-hoc check. We've removed the in-code migration —
fresh DBs work, but anyone running a pre-`cell_view` SQLite file has to drop it
(or hand-run the ALTER) until this lands.

Plan:

- Add `alembic` to backend deps; `alembic init backend/queryview/migrations`
  wired to the same async SQLite engine `connect.py` builds.
- Autogenerate an initial revision from the current `SQLModel.metadata` so
  fresh installs land on it cleanly, plus a revision equivalent to
  `ALTER TABLE predefined_queries ADD COLUMN cell_view TEXT` for the rollout.
- Run `alembic upgrade head` once at application **lifecycle start** (the
  FastAPI lifespan handler) before any request is served. Drop the lazy
  `_ensure_schema()` calls; Alembic + the lifespan hook becomes the single
  source of truth.

## Related docs

- [api.md](./api.md) — backend JSON API.
- [connect.md](./connect.md) — connecting, storage, sessions.
- [queryview.md](./queryview.md) — the single-prompt page concept.
- [query.md](./query.md) — running queries: pagination, predefined queries, CSV.
