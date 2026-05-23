# QueryView

Project skeleton: **Python** backend (**FastAPI + SQLModel**) + **Vite + React + TypeScript** SPA frontend with **Tailwind CSS**, plus **[Astral](https://github.com/lino-levan/astral)** end-to-end tests running on `deno test`.

## Layout

```
.
├── backend/         # Python FastAPI + SQLModel app exposing /api/*
├── frontend/        # Vite + React + TS + Tailwind v4 SPA
├── e2e/             # Astral browser tests (deno test)
└── deno.json        # Root tasks (frontend build, dev orchestration, e2e)
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — runs the Python backend (it manages the
  Python toolchain and dependencies for you).
- [Node.js](https://nodejs.org) 20+ (with npm) — installs and runs the Vite
  frontend.
- [Deno](https://docs.deno.com/runtime/getting_started/installation/) 2.x — the
  root task runner (`deno task …`) and the runner for the Astral e2e suite
  (`deno test`).

npm manages the frontend; uv handles the backend's Python virtualenv and
dependencies; Deno runs the root tasks and the Astral e2e browser (via JSR).

## Install

Install the backend's Python dependencies (uv reads `backend/pyproject.toml`):

```bash
uv sync --project backend
```

Install the frontend's npm dependencies (npm reads `frontend/package.json`):

```bash
npm --prefix frontend install
```

The first `deno task test:e2e` run downloads a Chromium binary into Astral's cache; no extra step is needed.

## Run dev servers

Run backend and frontend together:

```bash
deno task dev
```

Or individually:

```bash
deno task backend    # uvicorn --reload on http://localhost:8000
deno task frontend   # http://localhost:5173
```

The Vite dev server proxies `/api/*` to the FastAPI backend, so the SPA can call the API on the same origin.

## Build & preview production

```bash
deno task build      # produces frontend/dist/
deno task start      # SERVE_STATIC=1, FastAPI serves dist/ + /api on :8000
deno task preview    # build && start in one shot
```

In production there is no Vite — the FastAPI backend serves the bundled SPA from `frontend/dist/` and falls back to `index.html` for any unknown non-`/api` path so client-side routing works. Override the dist location with `STATIC_ROOT=/path/to/dist`.

## End-to-end tests

Start the dev servers (`deno task dev`) in one terminal, then in another:

```bash
deno task test:e2e
```

Override the target URL with `BASE_URL=http://localhost:4173 deno task test:e2e` (e.g. to test a built preview).

## API

See [docs/api.md](docs/api.md) for the full endpoint reference.

The single-page prompt UI is described in [docs/queryview.md](docs/queryview.md);
connecting (`new <type>` / `connect <name>`), SQLite persistence, and session
auto-connect are specified in [docs/connect.md](docs/connect.md).

Connections are stored in SQLite (`backend/queryview.db`, override with
`DB_PATH`); the backend writes that file and a local password-encryption key
(`backend/queryview.db.key`, override with `DB_KEY_PATH`).
