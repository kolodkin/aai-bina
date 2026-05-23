# QueryView

Project skeleton: **Python** backend (**FastAPI + SQLModel**) + **Vite + React + TypeScript** SPA frontend with **Tailwind CSS**, plus **[Playwright](https://playwright.dev)** end-to-end tests.

## Layout

```
.
├── backend/         # Python FastAPI + SQLModel app exposing /api/*
├── frontend/        # Vite + React + TS + Tailwind v4 SPA
├── e2e/             # Playwright browser tests
└── package.json     # Root tasks (dev orchestration, build, e2e) + Playwright
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — runs the Python backend (it manages the
  Python toolchain and dependencies for you).
- [Node.js](https://nodejs.org) 20+ (with npm) — runs the root tasks, the Vite
  frontend, and the Playwright e2e suite.

npm runs the frontend, the root task scripts, and the Playwright e2e browser;
uv handles the backend's Python virtualenv and dependencies.

## Install

Install the backend's Python dependencies (uv reads `backend/pyproject.toml`):

```bash
uv sync --project backend
```

Install the frontend's npm dependencies (npm reads `frontend/package.json`):

```bash
npm --prefix frontend install
```

Install the root dev dependencies (the task runner + Playwright) and its browser:

```bash
npm install
npx playwright install chromium
```

## Run dev servers

Run backend and frontend together:

```bash
npm run dev
```

Or individually:

```bash
npm run backend    # uvicorn --reload on http://localhost:8000
npm run frontend   # http://localhost:5173
```

The Vite dev server proxies `/api/*` to the FastAPI backend, so the SPA can call the API on the same origin.

## Build & preview production

```bash
npm run build      # produces frontend/dist/
npm run start      # SERVE_STATIC=1, FastAPI serves dist/ + /api on :8000
npm run preview    # build && start in one shot
```

In production there is no Vite — the FastAPI backend serves the bundled SPA from `frontend/dist/` and falls back to `index.html` for any unknown non-`/api` path so client-side routing works. Override the dist location with `STATIC_ROOT=/path/to/dist`.

## End-to-end tests

Start the dev servers (`npm run dev`) in one terminal, then in another:

```bash
npm run test:e2e
```

Override the target URL with `BASE_URL=http://localhost:4173 npm run test:e2e` (e.g. to test a built preview). To run the full suite against a real ClickHouse the way CI does, use `scripts/setup.sh`.

## API

See [docs/api.md](docs/api.md) for the full endpoint reference.

The single-page prompt UI is described in [docs/queryview.md](docs/queryview.md);
connecting (`new <type>` / `connect <name>`), SQLite persistence, and session
auto-connect are specified in [docs/connect.md](docs/connect.md).

Connections are stored in SQLite (`backend/queryview.db`, override with
`DB_PATH`); the backend writes that file and a local password-encryption key
(`backend/queryview.db.key`, override with `DB_KEY_PATH`).
