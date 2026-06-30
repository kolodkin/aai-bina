# QueryView Co-Edit — Design

**Date:** 2026-06-29
**Status:** Approved (design); pending implementation plan

## Summary

Let an agent and a human collaborate on the same QueryView query panel without
clobbering each other, via a **server-side edit lock** plus **persisted query
presentation**. The agent proposes (pushes a query + presentation); the human
refines and commits (Save); the agent reads the committed result and continues.
Turn-based, backend-mediated — no WebSocket, no live conflict resolution.

## Goals

- A single server-side lock serializes live edits to a session's panel.
- The contended side gets a clear, owner-named rejection
  (`"blocked, user editing"` / `"blocked, agent editing"`).
- A predefined query persists its full presentation: SQL, `cell_view`, and
  (new) `order_by` + selected `fields`.
- The browser shows an "agent update" toast when an agent push lands.
- Every push/save validates its structured JSON (fail-fast).

## Non-goals (explicitly out of scope)

- True simultaneous editing / CRDT / operational transforms.
- The agent reacting to *unsaved* live human edits in real time (the LLM is
  turn-based and cannot be auto-woken — see Communication Topology).
- Multi-process / multi-worker lock sharing (the existing SSE relay is already
  single-process; same constraint inherited here).
- A lock-status endpoint — the agent learns the lock state from push responses
  and backs off/retries.

## Background: why turn-based, backend-mediated

Confirmed against the MCP spec (2025-06-18) and corroborated by web research:

- Official MCP transports are **stdio** and **Streamable HTTP**. WebSocket is
  **not** a standard transport (only proposed: SEP-1287/1288).
- MCP is bidirectional at the protocol level (server→client notifications,
  sampling, elicitation, `resources/subscribe`).
- **But an unsolicited server message does not wake a turn-based LLM.** The
  agent only "sees" external state changes on its next turn. So no transport —
  WebSocket included — makes the agent react live. The bottleneck is the agent's
  turn model, not the wire.

Therefore: real-time only where a client can actually react (server→browser via
SSE); poll/retry for the agent half.

## Communication topology

There is no direct agent↔browser channel. The **backend is the hub**; both
clients rendezvous through it.

```
        agent  ──push_query / read─────────▶  BACKEND  ──SSE 'query'──▶  browser
   (MCP, turn-based)                          (hub +          (real-time, reacts instantly)
        ▲                                      lock +
        └────── push response / GET ───────────state)  ◀──lock + Save (HTTP)── browser
          (agent learns here, on its turn)
```

- **Agent → browser: real-time** via the existing SSE relay (`'query'` event).
  The browser applies the push and raises the toast.
- **Browser → agent: not real-time (correct).** The browser talks only to the
  backend (lock acquire/release, Save). The agent discovers human state on its
  own turn: (a) a push returns `"blocked, user editing"`, or (b) it `GET`s the
  committed query after the human Saves.

### Co-edit cycle

1. Agent `push_query(...)` → validate + lock-check → SSE → browser applies + toast.
2. Human focuses panel → `POST /api/remote/lock {action:"acquire"}` (owner=human).
3. Agent pushes again → `"blocked, user editing"` → backs off.
4. Human edits, clicks **Save** → upsert to DB (sql + order_by + fields + cell_view).
5. Human blurs → `POST /api/remote/lock {action:"release"}` (owner=none).
6. Agent retries push (succeeds) **or** `GET`s the committed query to read changes.

## Architecture & components

### Backend

- **`remote.py`** — owns the lock. Channels already live here keyed by remote
  id; each channel gains `lock_owner: None|"human"|"agent"` and
  `lock_touched: float` (monotonic). New functions:
  - `acquire(id, owner)` → free or already-yours: set owner, touch, ok. Held by
    other & unexpired: `(False, "blocked, <owner> editing")`.
  - `release(id, owner)` → clears only if you own it.
  - Lock guard consumed by the push path.
  - TTL: human lock auto-expires 30s after `lock_touched`; refreshed by the
    browser heartbeat while focused.
  - Existing `unregister(id)` (closed tab / dropped SSE) drops the channel and
    thus the lock.
- **`main.py`**
  - New `POST /api/remote/lock` — `{session_id, action: "acquire"|"release"}`;
    owner is always `"human"` (the browser).
  - `remote_push` (HTTP) validates payload, consults the lock, returns the
    structured blocked/invalid result.
  - Predefined-query save/list endpoints accept and return `order_by` + `fields`.
- **`mcp_server.py`** — `push_query` surfaces the same validation + blocked
  results so the agent sees `"blocked, user editing"` / `"invalid …"`.
- **`queries.py`** — `PredefinedQuery` gains `order_by` + `fields` columns;
  `save_predefined_query` / `list_predefined_queries` read/write them.
- **Alembic migration** — add the two columns (nullable JSON text).
- **Shared validator module** — used by the MCP push, the HTTP push, and the
  predefined-query save (see Validation).

### Frontend

- **`QueryView.tsx`**
  - Panel-level focus tracker: `focusin` on any editable item → acquire lock for
    `remoteId`; **debounced** `focusout` → release; heartbeat (~10s) while
    focused → re-acquire to refresh TTL.
  - Save sends `order_by` + `fields`; load/select restores them (alongside the
    already-wired SQL/cell_view/name).
- **`App.tsx`** — the existing `query` SSE listener raises the "agent update"
  toast (transient, ~3s, auto-dismiss). Only agent pushes trigger it; the
  human's own edits do not.

## Data model change — `predefined_queries`

| column | type | notes |
|---|---|---|
| `order_by` | TEXT (JSON) NULL | e.g. `[{"name":"id","dir":"DESC"}]` |
| `fields` | TEXT (JSON) NULL | visible columns, e.g. `["source"]`; NULL = show all |

JSON-encoded text (matches how the values already travel over the wire),
nullable so existing rows and "no selection" remain valid. Durable presentation
lives in SQLite; ephemeral lock/presence lives in RAM.

## Edit lock

| | Acquire | Release | While held, other side gets |
|---|---|---|---|
| **Human** | focus any editable item in panel | blur out of panel (debounced) + TTL backstop + SSE-disconnect | agent push → `"blocked, user editing"` |
| **Agent** | per push (atomic) | immediately after enqueue | human acquire → `"blocked, agent editing"` (effectively never) |

**Push flow** (shared by MCP and HTTP `remote.push`), in order:

1. **Validate** payload → invalid ⇒ `(False, "invalid order_by: …")`, nothing delivered.
2. **Lock check** → human holds (unexpired) ⇒ `(False, "blocked, user editing")`.
3. Otherwise mark `agent` transiently, enqueue payload, clear → `(True, "delivered")`.
   Atomic; a racing human `acquire` is serialized by the lock primitive.

**Storage:** RAM, on the existing in-memory channel object in `remote.py`. The
lock is bound to a live SSE connection; persisting it would create stale locks
on restart/disconnect.

## Input validation (fail-fast)

Shared validator, run on every path (MCP push, HTTP push, predefined save):

| field | rule | on violation |
|---|---|---|
| `order_by[]` | object with `name` (non-empty str, **no backtick**) + `dir` ∈ {`ASC`,`DESC`} (case-insensitive) | **reject** |
| `fields[]` | list of non-empty strings | **reject** |
| `limit`/`offset` | int, `0 ≤ limit ≤ MAX`, `offset ≥ 0` | clamp |
| `cell_view` | lenient — stored verbatim; broken YAML falls back to plain render (documented) | keep (optional non-fatal warning) |

- **Reject = fail-fast:** return `{ok:false, message:"invalid …"}`; nothing is
  applied or persisted. The agent sees the exact problem.
- **`order_by` is security-sensitive** (flows into SQL `ORDER BY`). Validation is
  defense-in-depth; the real guard is the SQL applier backtick-quoting the name
  and whitelisting the direction. Implementation must confirm the **server-side**
  query path enforces this, not just the client picker.

## Toast

The existing `query` SSE listener fires on every agent push → transient toast
("Agent updated the query"), ~3s auto-dismiss, small local component, no new
dependency. Human edits never trigger it.

## Error handling & edge cases

| Case | Behavior |
|---|---|
| Push while human holds lock | `(False, "blocked, user editing")` — nothing delivered. |
| Invalid `order_by`/`fields` | `(False, "invalid …")` — no delivery, no DB write. |
| Human focuses during atomic agent push | serialized server-side; loser gets the contended reason (effectively never visible). |
| Closed tab / dead SSE | existing `unregister` frees the lock. |
| Focused-but-idle tab | heartbeat keeps TTL alive; if it stops, lock expires after 30s. |
| `focusout`→`focusin` between fields | debounced release prevents thrash. |
| Push to unknown/inactive session | unchanged: `(False, "unknown or inactive session")`. |
| **Human blurs without Save** | lock releases; **unsaved UI edits are not persisted** and a later agent push may overwrite them. Accepted: blur = "done for now"; Save is how you keep it. |
| Agent `GET`s mid human-edit | returns last *saved* (DB) state, never the live unsaved panel. |

## Testing

**Backend (pytest, `test_remote.py` style):**

- Lock: acquire→push blocked; release→push delivers; `unregister` frees lock;
  TTL expiry frees lock; agent atomic push leaves lock free afterward.
- Validation: bad `dir`, missing `name`, backtick in `name`, non-string
  `fields` → `(False, "invalid …")`, nothing enqueued; limit/offset clamped.
- Parity: HTTP `/api/remote/push` and the MCP path enforce identical
  validation + lock behavior.
- Persistence: save with `order_by`/`fields` round-trips via
  `list_predefined_queries`; NULL for legacy rows; migration applies cleanly.

**Frontend (vitest):**

- Pure serialization/validation helpers for `order_by`/`fields`.
- Pushed query restores `order_by`/`fields`/`cell_view`/name (extend existing
  apply-pushed coverage).
- Focus tracker: focusin→acquire, debounced focusout→release.

**Out of scope (documented, not tested):** multi-process locking; concurrent
human+agent race beyond the serialized-lock guarantee.

## Known constraints

- **Single backend process.** Channels and the lock are in-process RAM, exactly
  like the existing SSE relay. Scaling to multiple workers would require a shared
  store (e.g. Redis) for both — a pre-existing relay limitation, not introduced here.
