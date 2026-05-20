# `connect clickhouse`

Typing `connect clickhouse` into the prompt reveals a connection form. The user
fills it in, tests the connection, and on success the page shows a green
connection indicator in the top-left.

## Trigger

- Command: `connect clickhouse` (case-insensitive, whitespace-trimmed).
- On match, the ClickHouse connection form renders below the prompt.

## Form fields

| Field    | Default     | Notes                                          |
| -------- | ----------- | ---------------------------------------------- |
| Name     | `clickhouse`| Label for the connection; shown in the indicator. |
| Host     | `localhost` | ClickHouse host.                               |
| Port     | `8123`      | ClickHouse HTTP interface port.                |
| Username | `default`   | ClickHouse user.                               |
| Password | *(empty)*   | ClickHouse password.                           |

Below the fields is a **Test connection** button.

## Test connection

Clicking **Test connection** POSTs the form to the backend, which connects to
ClickHouse and reports back.

### Request

```
POST /api/clickhouse/test
Content-Type: application/json

{ "host": "localhost", "port": 8123, "username": "default", "password": "" }
```

### Backend behaviour

- Validates `host` (non-empty) and `port` (integer in `1..65535`).
- Issues `GET http://{host}:{port}/?query=SELECT 1` to the ClickHouse HTTP
  interface, authenticating with HTTP Basic auth (`username:password`).
- Aborts after a 5s timeout.

### Response

Always `200` for a request that ran (validation errors are `400`):

```json
{ "ok": true,  "message": "Connected — SELECT 1 returned 1" }
{ "ok": false, "message": "connection timed out" }
```

## Connected indicator

When the test succeeds, a pill appears in the **top-left** of the page:

> 🟢 &nbsp; *{name}*

- A green circle (`bg-emerald-500`) signals a live connection.
- The text is the **Name** field value (falls back to `clickhouse` if blank).
- The indicator stays visible while the connection is active.

## CI

CI runs a real `clickhouse/clickhouse-server` service container (HTTP port
`8123`) so the e2e test exercises an actual `SELECT 1`. The e2e run sets
`EXPECT_CLICKHOUSE_OK=1`, which makes the test assert the connection succeeds
and the indicator appears. Without that flag the test only asserts the UI flow
renders a result, so it stays green in environments without ClickHouse.
