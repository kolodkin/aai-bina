# `connect clickhouse`

Typing `connect clickhouse` into the prompt opens a connection form. From there
the user can **test** a connection (a throwaway check) or **connect** (open a
steady connection that is saved and becomes the session's active connection).
Connections are persisted in SQLite, and the latest active one is re-connected
automatically when a session starts.

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

## Trigger

- Command: `connect clickhouse` (case-insensitive, whitespace-trimmed).
- On match, the connection form renders below the prompt.

## Connection form

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
prompt ── type "connect clickhouse" ──▶ connection form
                                          │
                          ┌── Test ───────┤   (inline pass/fail, stays here)
                          │               │
                          └── Connect ────┴──▶ prompt + database picker
                                                      │
                                       pick a database │
                                                      ▼
                                       🟢 connected - <database>
```

## Database picker

After connecting, the prompt view shows the list of databases returned by
`SHOW DATABASES`. Selecting one:

- sets the session's selected database,
- persists it on the saved connection (`POST /api/clickhouse/database`),
- shows the top-left indicator `🟢 connected - <database>`.

## Persistence (SQLite)

Connection details are stored in a SQLite database (default
`backend/queryview.db`, override with `DB_PATH`). This requires the backend to
run with `--allow-write` (and `--allow-read`) for the DB file.

Schema:

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
value is `base64(iv ‖ ciphertext)`, never plaintext. The key is resolved once at
startup:

- `DB_ENCRYPTION_KEY` — base64 of 32 bytes, if set (use this in CI/shared envs);
- otherwise a key is generated and written to `<DB_PATH>.key` (gitignored,
  mode `600`), overridable with `DB_KEY_PATH`.

If the key changes (or a row predates encryption) the value can't be decrypted;
auto-connect simply skips that connection and the user reconnects, which
re-encrypts it with the current key.

## Session start / auto-connect

The active connection lives at the session level. When a session starts the
backend reads the **latest active** connection from SQLite and attempts to
connect to it:

- `GET /api/session` returns the current session state and, if nothing is active
  yet, lazily auto-connects to the latest active connection.
- On success the SPA loads already connected — prompt + database picker, with
  the previously selected database pre-selected and the indicator shown.
- On failure (server down, bad credentials) the SPA falls back to the empty
  prompt; the saved connection is left in place to retry.

## API

| Method | Path                          | Body                                   | Result |
| ------ | ----------------------------- | -------------------------------------- | ------ |
| POST   | `/api/clickhouse/test`        | `{host,port,username,password}`        | `{ok, message}` — test only |
| POST   | `/api/clickhouse/connect`     | `{name,host,port,username,password}`   | `{ok, name, databases}` \| `{ok:false, message}`; saves + activates |
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
