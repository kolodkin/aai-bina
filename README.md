# QueryView

Project skeleton: **Deno** backend + **Vite + React + TypeScript** SPA frontend with **Tailwind CSS**, plus **[Astral](https://github.com/lino-levan/astral)** end-to-end tests running on `deno test`.

## Layout

```
.
├── backend/         # Deno HTTP server (Deno.serve) exposing /api/*
├── frontend/        # Vite + React + TS + Tailwind v4 SPA
├── e2e/             # Astral browser tests (deno test)
└── deno.json        # Root tasks
```

## Prerequisites

- [Deno](https://docs.deno.com/runtime/getting_started/installation/) 2.x

That's it — no Node required. Deno acts as the package manager for the frontend per the [official Deno + Vite + React tutorial](https://docs.deno.com/examples/react_tutorial/), and Astral handles the e2e browser via JSR.

## Install

Install the frontend's npm dependencies (Deno reads `package.json`):

```bash
deno install --cwd frontend
```

The first `deno task test:e2e` run downloads a Chromium binary into Astral's cache; no extra step is needed.

## Run dev servers

Run backend and frontend together:

```bash
deno task dev
```

Or individually:

```bash
deno task backend    # http://localhost:8000
deno task frontend   # http://localhost:5173
```

The Vite dev server proxies `/api/*` to the Deno backend, so the SPA can call the API on the same origin.

## Build & preview production

```bash
deno task build      # produces frontend/dist/
deno task start      # SERVE_STATIC=1, Deno serves dist/ + /api on :8000
deno task preview    # build && start in one shot
```

In production there is no Vite — the Deno backend serves the bundled SPA from `frontend/dist/` and falls back to `index.html` for any unknown non-`/api` path so client-side routing works. Override the dist location with `STATIC_ROOT=/path/to/dist`.

## End-to-end tests

Start the dev servers (`deno task dev`) in one terminal, then in another:

```bash
deno task test:e2e
```

Override the target URL with `BASE_URL=http://localhost:4173 deno task test:e2e` (e.g. to test a built preview).

## API

| Method | Path          | Description              |
| ------ | ------------- | ------------------------ |
| GET    | `/api/health` | Service health check     |
| GET    | `/api/items`  | List items               |
| POST   | `/api/items`  | Create item `{ name }`   |

Items live in memory and reset when the backend restarts — replace `backend/main.ts` with real storage when you are ready.
