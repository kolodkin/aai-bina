# QueryView — single-prompt page concept

QueryView is a single page. There is no navigation, no sidebar, no dashboard.
The whole surface is one centered prompt — the user types a command and the
page reacts to it inline.

## Layout

```
┌─────────────────────────────────────────────┐
│ 🟢 clickhouse        ← connection indicator   │
│                                               │
│                                               │
│                  QueryView                    │
│        ┌─────────────────────────────┐        │
│        │  Type a command…            │  ← prompt
│        └─────────────────────────────┘        │
│                                               │
│        (command output renders here)          │
│                                               │
└─────────────────────────────────────────────┘
```

- **Heading** — `QueryView`, centered.
- **Prompt** — a single text input, centered on the page, auto-focused.
  Submitting (Enter) interprets the typed text as a command.
- **Inline response** — the result of a command renders directly under the
  prompt. The prompt itself stays in place; the page does not navigate.
- **Connection indicator** — a small pill in the **top-left** corner. It is
  hidden until a connection is established, then shows a green circle 🟢 next
  to the connection name (see [connect.md](./connect.md)).

## Interaction model

1. The page opens focused on the prompt.
2. The user types a command and presses Enter.
3. Recognized commands swap in their own UI below the prompt.
4. Unrecognized input shows a short hint instead of an error page.

## Commands

| Command             | Effect                                              |
| ------------------- | --------------------------------------------------- |
| `connect clickhouse`| Reveals the ClickHouse connection form. See below.  |

Anything else shows: `Unknown command “…”. Try “connect clickhouse”.`

Command matching is case-insensitive and trims surrounding whitespace.

## Design principles

- **One thing at a time.** The prompt is the only persistent control. Each
  command owns the space beneath it.
- **No dead ends.** Unknown input is guided, never punished.
- **State is visible.** Once connected, the top-left indicator makes the active
  connection obvious from anywhere on the page.
