# QueryView

Project skeleton: **Deno** backend + **Vite + React + TypeScript** SPA frontend with **Tailwind CSS**, plus **Playwright** end-to-end tests.

## Layout

```
.
├── backend/         # Deno HTTP server (Deno.serve) exposing /api/*
├── frontend/        # Vite + React + TS + Tailwind v4 SPA
├── e2e/             # Playwright end-to-end tests
└── deno.json        # Root tasks
```

## Prerequisites

- [Deno](https://docs.deno.com/runtime/getting_started/installation/) 2.x
- A modern browser (Chromium is installed automatically by Playwright)

Node is **not** required — Deno acts as the package manager for the frontend, per the [official Deno + Vite + React tutorial](https://docs.deno.com/examples/react_tutorial/).

## Install

Install npm dependencies (Deno reads `package.json` and writes a local `node_modules/`):

```bash
deno install --cwd frontend
deno install --cwd e2e
deno run -A --cwd e2e npm:playwright install --with-deps chromium
```

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

## Build

```bash
deno task build      # produces frontend/dist/
```

## End-to-end tests

Start the dev servers (`deno task dev`) in one terminal, then in another:

```bash
deno task test:e2e
```

Or have Playwright start the servers itself:

```bash
MANAGE_SERVERS=1 deno task test:e2e
```

## API

| Method | Path          | Description              |
| ------ | ------------- | ------------------------ |
| GET    | `/api/health` | Service health check     |
| GET    | `/api/items`  | List items               |
| POST   | `/api/items`  | Create item `{ name }`   |

Items live in memory and reset when the backend restarts — replace `backend/main.ts` with real storage when you are ready.
