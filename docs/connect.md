# Connecting

Connections have two halves: a **type** (the driver, e.g. `clickhouse`) and a
**name** (your label, e.g. `clickhouse`, `prod-ch`). You create a connection
once with `new <type>`, then open it by name with `connect <name>`. Connections
are persisted in SQLite and the latest active one is re-connected automatically
when a session starts.

## Commands

| Command          | Effect |
| ---------------- | ------ |
| `new clickhouse` | Open the form to create a new ClickHouse connection. |
| `connect <name>` | Open the saved connection `<name>` and show its database picker. |

All matching is case-insensitive and whitespace-trimmed. An unknown command
shows a hint (`Try "new clickhouse" or "connect <name>"`); `connect <name>` for
an unknown name reports `no connection named "<name>"`.

## Concepts

- **Test connection** — a throwaway connectivity check (`SELECT 1`). It reports
  pass/fail and nothing else: it does **not** save the connection, does not open
  a steady connection, and does not change what the session is connected to.
- **Connect** — opens a *steady* connection: it validates, lists the databases,
  **saves** the connection to SQLite, marks it the latest active, and makes it
  the session's active connection. The UI then returns to the single prompt with
  a database picker.
- **Active connection** — held at the **session** level (see
  [queryview.md](./queryview.md)). One per session.
- **Database selection** — after connecting, the user picks a database. Only
  then does the top-left indicator read `🟢 connected - <database>`. The choice
  is remembered with the connection.

## Creating a connection (`new <type>`)

`new clickhouse` renders the connection form below the prompt.

### Connection form

| Field    | Default     | Notes                                          |
| -------- | ----------- | ---------------------------------------------- |
| Name     | `clickhouse`| Label for the connection (unique key in storage). |
| Host     | `localhost` | ClickHouse host.                               |
| Port     | `8123`      | ClickHouse HTTP interface port.                |
| Username | `default`   | ClickHouse user.                               |
| Password | *(empty)*   | ClickHouse password.                           |

Two actions:

- **Test connection** — `POST /api/clickhouse/test`. Shows a pass/fail message
  inline. No side effects.
- **Connect** — `POST /api/clickhouse/connect`. On success the form closes and
  the prompt view returns with a database picker.

## Flow

```
prompt ── "new clickhouse" ──▶ connection form
                                 │
                 ┌── Test ───────┤   (inline pass/fail, stays here)
                 │               │
                 └── Connect ────┴──▶ prompt "connect <name>" + database picker
                                              │
                              pick a database  │   (picker collapses)
                                              ▼
                                  🟢 connected - <database>

prompt ── "connect <name>" ──▶ opens saved <name> ──▶ database picker ──▶ pick
```

## Opening a saved connection (`connect <name>`)

`connect <name>` looks up the saved connection by name, opens it (lists its
databases), makes it the active session connection, and shows the database
picker. Pick a database from the picker to finish.

## Database picker

After connecting, the prompt view shows the list of databases returned by
`SHOW DATABASES`. Selecting one:

- sets the session's selected database,
- persists it on the saved connection (`POST /api/clickhouse/database`),
- collapses the picker and shows the top-left indicator `🟢 connected - <database>`,
- clears the prompt and switches its placeholder to `table <name> / query`,
  inviting the next action.

While the picker is open the prompt reads `connect <name>`; once a database is
selected the prompt clears and its placeholder (`table <name> / query`) prompts
for the next action.

## Persistence (SQLite)

Connection details are stored in a SQLite database (default
`backend/queryview.db`, override with `DB_PATH`) managed with SQLModel. The
backend creates the file and its schema on first use.

Schema (the `connections` table SQLModel maps to):

```sql
CREATE TABLE connections (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT NOT NULL UNIQUE,
  host           TEXT NOT NULL,
  port           INTEGER NOT NULL,
  username       TEXT NOT NULL,
  password       TEXT NOT NULL,
  database       TEXT,            -- last selected database (nullable)
  last_active_at INTEGER NOT NULL -- unix ms; the max is the "latest active"
);
```

- **Connect** upserts the row by `name` and bumps `last_active_at`.
- **Selecting a database** updates `database` for that row.
- **Latest active** = the row with the greatest `last_active_at`.

### Password encryption

The `password` column is **encrypted at rest** with AES-256-GCM; the stored
value is `base64(iv ‖ ciphertext)` (AES-GCM appends its 16-byte tag to the
ciphertext), never plaintext. The key is resolved once and memoized on first
use:

- `DB_ENCRYPTION_KEY` — base64 of 32 bytes, if set (use this in CI/shared envs);
- otherwise a key is generated and written to `<DB_PATH>.key` (gitignored,
  mode `600`), overridable with `DB_KEY_PATH`.

If the key changes (or a row predates encryption) the value can't be decrypted;
auto-connect simply skips that connection and the user reconnects, which
re-encrypts it with the current key.

## Sessions, cookies & auto-connect

Each browser session has **one active connection**, held server-side and keyed
by a session **cookie** (`qv_session`, HttpOnly, set on first request). Different
sessions (browsers / profiles) connect independently — one session switching
connections doesn't affect another. Saved connections themselves are shared
(stored once in SQLite); the *active* one is per session.

When a session starts (a cookie the backend hasn't seen) it reconnects the
**latest active** connection from SQLite, so a fresh session resumes where the
last one left off:

- `GET /api/session` returns this session's state and, for an unseen cookie,
  lazily auto-connects to the latest active connection.
- On success the SPA loads already connected — prompt + database picker, with
  the previously selected database pre-selected and the indicator shown.
- On failure (server down, bad credentials) the SPA falls back to the empty
  prompt; the saved connection is left in place to retry.

To open a **specific** connection on load instead of the latest active one,
pass it as a query param: `…/?connection=<name>` opens that saved connection
(equivalent to `connect <name>`). The SPA then cleans the URL so a later reload
resumes normally.

The per-session state lives in memory, so a backend restart drops it; the next
request gets a new session that auto-connects the latest active connection.

## API

| Method | Path                          | Body                                   | Result |
| ------ | ----------------------------- | -------------------------------------- | ------ |
| POST   | `/api/clickhouse/test`        | `{host,port,username,password}`        | `{ok, message}` — test only |
| POST   | `/api/clickhouse/connect`     | `{name,host,port,username,password}`   | `{ok, name, databases}` \| `{ok:false, message}`; saves + activates (`new <type>` form) |
| POST   | `/api/clickhouse/open`        | `{name}`                               | `{ok, name, databases}` \| `{ok:false, message}`; opens a saved connection (`connect <name>`) |
| POST   | `/api/clickhouse/database`    | `{database}`                           | `{ok}`; sets the session/connection database |
| GET    | `/api/session`                | —                                      | `{connected, name?, databases?, database?}`; auto-connects latest active |

All validate `host` (non-empty) and `port` (integer `1..65535`); validation
errors return `400`. ClickHouse queries run over the HTTP interface with HTTP
Basic auth and a 5s timeout.

## CI

CI runs a real `clickhouse/clickhouse-server` service container so the e2e test
exercises an actual connection. With `EXPECT_CLICKHOUSE_OK=1` the test asserts
that connecting succeeds, a database can be selected, and the indicator shows
`connected - <database>`.
