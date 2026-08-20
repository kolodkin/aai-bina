# QueryView

Project skeleton: **Python** backend (**FastAPI + SQLModel**) + **Vite + React + TypeScript** SPA frontend with **Tailwind CSS**, plus **[Playwright](https://playwright.dev)** end-to-end tests.

## Quick start

Run the released package — API + bundled SPA on http://localhost:8000:

```bash
uvx aaibina
```

`--port` (or the `PORT` env var) picks the listen port, default 8000:
`uvx aaibina --port 9000`.

Or run the container image — every release publishes
`ghcr.io/kolodkin/aaibina` to GHCR for `linux/amd64` and `linux/arm64`, tagged
`vX.Y.Z` and (for non-pre-releases) `latest`:

```bash
docker run -p 8000:8000 ghcr.io/kolodkin/aaibina:latest
```

State (the SQLite DB and its encryption key) lives in `/home/aaibina`; mount a
volume there to persist it across containers:
`docker run -p 8000:8000 -v aaibina-data:/home/aaibina ghcr.io/kolodkin/aaibina:latest`.

## Layout

```
.
├── backend/         # Python FastAPI + SQLModel app exposing /api/* (queryview package)
├── frontend/        # Vite + React + TS + Tailwind v4 SPA (npm workspace)
├── e2e/             # Playwright (pytest) browser tests
├── pyproject.toml   # Backend deps + console script + e2e `test` group (uv)
└── package.json     # npm workspace root: dev orchestration + frontend build
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — runs the Python backend and the Playwright
  (pytest) e2e suite (it manages the Python toolchain and dependencies for you).
- [Node.js](https://nodejs.org) 20+ (with npm) — runs the root tasks and the
  Vite frontend.

npm runs the frontend and the root task scripts; uv handles the backend's and
e2e suite's Python virtualenv and dependencies.

## Install

Install the backend's Python dependencies (uv reads the root `pyproject.toml`;
the package lives in `backend/queryview`):

```bash
uv sync
```

Install the JavaScript dependencies for the frontend workspace:

```bash
npm install
```

Install the e2e tooling (the `test` dependency group) and fetch the Playwright
browser:

```bash
uv sync --group test
uv run --group test playwright install chromium
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

The e2e suite is [pytest-playwright](https://playwright.dev/python/docs/test-runners),
installed via the `test` dependency group and run through `uv`.

Start the dev servers (`npm run dev`) in one terminal, then in another:

```bash
uv run --group test pytest
```

Override the target URL with `BASE_URL=http://localhost:4173 uv run --group test pytest` (e.g. to test a built preview). To run the full suite against a real ClickHouse the way CI does, use `scripts/setup.sh`.

## Release to PyPI

The **Publish to PyPI** workflow (`.github/workflows/publish.yaml`, manual
dispatch with a `vX.Y.Z` tag input) builds the SPA into the wheel
(`queryview/static/`), then gates the release on the installed wheel: an HTTP
smoke test, the packaged backend test suite (`pytest --pyargs queryview`), and
the Playwright e2e suite driving the packaged server (skippable via the
`skip-e2e` input for emergencies). It then publishes
[`aaibina`](https://pypi.org/project/aaibina/) via PyPI trusted publishing,
pushes the tag, and creates the GitHub release. The package version comes
from the tag (no version bump in `pyproject.toml`).

An installed wheel serves the bundled UI by default — see
[Quick start](#quick-start).

## MCP server

The backend mounts a FastMCP server (Streamable HTTP) at
`http://localhost:8000/mcp`. There is nothing extra to start — it runs inside
the server process (`uvx aaibina`, `npm run dev`, ...). Hook up an MCP client,
e.g.:

```bash
claude mcp add --transport http queryview http://localhost:8000/mcp
```

Tools: `run_query` (read-only SQL, rows returned to the agent), `push_query`
and `push_dashboard` (fill a live browser session), `list_queries` /
`list_dashboards`, and `git_store` / `git_history` / `git_restore` (workspace
git backups). The push tools target an **armed** browser session: enable
"Allow remote control" from the agent icon next to the connection pill and use
the session id it shows. See [docs/remote.md](docs/remote.md) for the full
protocol.

## API

See [docs/api.md](docs/api.md) for the full endpoint reference.

The single-page prompt UI is described in [docs/queryview.md](docs/queryview.md);
connecting (`new <type>` / `connect <name>`), SQLite persistence, and session
auto-connect are specified in [docs/connect.md](docs/connect.md).

Connections are stored in SQLite (`backend/queryview.db`, override with
`DB_PATH`); the backend writes that file and a local password-encryption key
(`backend/queryview.db.key`, override with `DB_KEY_PATH`).
