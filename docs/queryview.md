# QueryView — single-prompt page concept

QueryView is a single page. There is no navigation, no sidebar, no dashboard.
The whole surface is one centered prompt — the user types a command and the
page reacts to it inline.

## Layout

```
┌─────────────────────────────────────────────┐
│ 🟢 connected - default   ← connection status  │
│                                               │
│                  QueryView                    │
│        ┌─────────────────────────────┐        │
│        │  Type a command…            │  ← prompt
│        └─────────────────────────────┘        │
│                                               │
│        (each mode renders its UI here)        │
│                                               │
└─────────────────────────────────────────────┘
```

- **Heading** — `QueryView`, centered.
- **Prompt** — a single text input, centered on the page, auto-focused.
  Submitting (Enter) interprets the typed text as a command.
- **Inline response** — each command renders its own UI directly under the
  prompt (e.g. the connection form and database picker — see
  [connect.md](./connect.md)). The prompt stays in place; the page does not
  navigate.
- **Connection status** — the one element that persists across every mode: a
  small pill in the **top-left** corner. It is hidden until a database is
  selected on the active connection, then shows a green circle 🟢 followed by
  `connected - <database>`.

## Interaction model

1. The page opens focused on the prompt.
2. The user types a command and presses Enter.
3. Recognized commands swap in their own UI below the prompt.
4. Unrecognized input shows a short hint instead of an error page.

## Sessions

The active connection is **session state**, not global UI state:

- It is held per session at the backend, and persisted in SQLite (see
  [connect.md](./connect.md)).
- **At session start the app attempts to connect to the latest active
  connection.** The SPA asks `GET /api/session` on load; if a previously active
  connection re-connects, the page opens already connected — prompt + database
  picker, the previously selected database pre-selected, and the indicator
  shown. Otherwise it opens at the empty prompt.

## Commands

| Command          | Effect                                              |
| ---------------- | --------------------------------------------------- |
| `new clickhouse` | Reveals the form to create a new ClickHouse connection. |
| `connect <name>` | Opens the saved connection `<name>` and shows its database picker. |

Anything else shows a hint: `Try “new clickhouse” or “connect <name>”`.

Command matching is case-insensitive and trims surrounding whitespace. See
[connect.md](./connect.md) for the full connection flow.

## Design principles

- **One thing at a time.** The prompt is the only persistent control. Each
  command owns the space beneath it.
- **No dead ends.** Unknown input is guided, never punished.
- **State is visible.** Once a database is selected, the top-left indicator
  makes the active connection and database obvious from anywhere on the page.
- **Resumable.** Sessions reconnect to the last active connection on start, so
  the common case needs no typing at all.
