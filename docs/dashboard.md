# Dashboards (agent-authored, pushed to a live session)

A **dashboard** is an HTML layout plus a set of named SQL queries. An AI agent
authors one and pushes it into a live, armed QueryView browser session over MCP;
it is also persisted so it can be reopened later by name.

Responsibilities mirror the [remote-control](./remote.md) layer: the agent never
receives query results back. The **browser** (trusted code with a session) runs
the queries against a named connection and feeds the results into the
agent-authored HTML, which renders inside an isolated iframe.

## The page (`/dashboard`)

`/dashboard` is the second top-level page (the first is the query workflow at
`/queries`; see [queryview.md](./queryview.md)). It has:

- a **dropdown** of saved dashboards — selecting one sets `?name=<name>`, so the
  URL is shareable and the back button works;
- a **sandboxed iframe** that renders the selected dashboard.

Open it from the prompt with `dashboard` (just the dropdown) or `dashboard
<name>` (jump straight to one), from the corner nav, or by URL. Reopening
re-fetches the dashboard and re-runs its queries, so a reloaded or shared link
always shows live data.

## The `push_dashboard` MCP tool

The FastMCP server at `/mcp` (see [remote.md](./remote.md) for arming and the
session id) exposes:

- `push_dashboard(session_id, name, connection, html, queries)` — push the
  dashboard **draft** to the browser identified by `session_id`, which navigates
  to `/dashboard?name=<name>` and renders it. It does **not** persist — only the
  user's **Save** button in the dashboard view writes it to the store (mirrors
  `push_query`). Returns `{ok, pushed, message}`; an unknown/disarmed
  `session_id` reports `pushed:false`.

`session_id` and `connection` are distinct: `session_id` is the live browser to
push the preview to; `connection` is the saved [connection](./connect.md) the
queries run against (by name — a dashboard is self-contained and portable). Its
**stored database** is used, so select a database for that connection first, or
fully-qualify table names as `db.table`.

The REST mirror `POST /api/dashboards` takes the same fields (plus optional
`session_id`) and drives the same persist-and-push path (used by the e2e suite).
See [api.md](./api.md) for the full endpoint list.

## The `window.queries` contract for HTML authors

Before the dashboard HTML runs, the page injects the results as a `window.queries`
global — a **column-oriented** map:

```js
window.queries = {
  <query_name>: { <column_name>: [values, …] },
  …
}
```

So for a query named `sales` selecting a `revenue` column,
`window.queries.sales.revenue` is that column's values. Column order is preserved.
Load any chart library from a CDN inside the HTML. A minimal dashboard:

```html
<canvas id="c"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
  const { month, revenue } = window.queries.sales
  new Chart(document.getElementById('c'), {
    type: 'bar',
    data: { labels: month, datasets: [{ label: 'Revenue', data: revenue.map(Number) }] },
  })
</script>
```

## Dashboard parameters

A dashboard can declare **selectors** whose chosen values are substituted into its
queries, so an interactive dashboard re-queries per selection instead of
precomputing every result it might ever show. They live in a `params` list on the
dashboard (persisted in `dashboards.params`, written to `meta.yaml` by
[git sync](./gitsync.md)):

```yaml
params:
  - name: table
    kind: identifier
    options_sql: SELECT name FROM system.tables WHERE database = currentDatabase()
  - name: field
    kind: identifier
    options_sql: SELECT name FROM system.columns WHERE table = {table:literal}
  - name: category
    kind: dimension
```

with a query referencing the placeholders:

```sql
SELECT {field} AS value, {category} AS category, count() AS cnt
FROM {table} GROUP BY value, category
```

Three kinds, differing only in how a value is substituted:

| kind | substitutes as | example |
| --- | --- | --- |
| `value` (default) | quoted string literal, single quotes doubled | `{region}` → `'eu'` |
| `identifier` | backtick-quoted table/column | `{field}` → `` `city` `` |
| `dimension` | its own column when checked, `''` when not | `{category}` → `` `category` `` / `''` |

A placeholder can override its param's kind for one spot: **`{table:literal}`**
substitutes a string where the SQL wants one (`WHERE table = {table:literal}`)
while `FROM {table}` still gets the identifier. The casts are `literal` and
`identifier`; anything else is an error.

`identifier` exists because a literal would make `SELECT {field}` select a constant
string rather than the column. A value containing a backtick is rejected. A
`dimension` renders as a checkbox and needs no options: unchecked it substitutes an
empty literal, so the query keeps its shape and the column simply drops out of the
grouping.

As with [query params](./query.md), `options` and `options_sql` are mutually
exclusive, and the first option is selected for you unless the param opts out
with **`default: none`** — which leaves the selector empty and holds back every
query referencing it until a choice is made. Use it when the first option is an
arbitrary pick rather than a sensible default (any table, any column). A
dependent `options_sql` whose `{param}` is still unset simply resolves to no
options, so the dashboard opens waiting rather than erroring. An `options_sql` may itself
reference another `{param}` — so a field list can depend on the selected table —
and those are resolved in dependency order; a cycle is an error.

## Changing parameters: the `window.params` contract

Resolved selectors are injected alongside the results:

```js
window.params = [{ name, kind, options: [...], value }, …]
```

The page renders its own controls from that list and asks for a re-run with
`window.setParams({field: 'city', category: true})`. The host substitutes the
values into the dashboard's **own** queries, runs them, and posts the results
back; the page's `window.onQueryResults(results, params, message)` fires with the
new `window.queries` and `window.params`. The iframe is not reloaded, so it keeps
its state.

The page sends **values only** — never SQL — so query execution stays entirely in
the host, and a dashboard's SQL remains reviewable in `queries.yaml`. (A page may
still send its own SQL with `window.runQueries({name: sql})` for cases params
can't express; the host runs it against the dashboard's connection, never one the
page chooses.)

## Running the queries: `/api/runqueries`

The browser POSTs `{connection, queries}` to `/api/runqueries`, which runs each
query against the connection's stored database (wrapped in a paginated subselect
capped at 1000 rows) and returns column-oriented results.

It is **fail-fast**: if any query fails, the connection is unknown, or it has no
selected database, the whole request returns an HTTP error and the page shows a
dashboard-level error banner instead of partial panels. On success every named
query is present in `window.queries`. A failing query's message is prefixed with
its panel name (e.g. `churn: Unknown table …`) so it's clear which one to fix.

## Isolation & security

The HTML renders in `<iframe sandbox="allow-scripts" srcdoc=…>` **without**
`allow-same-origin`, giving it an opaque origin: it can run JS and load CDN
assets but cannot read the app's cookies, `localStorage`, or call `/api/*` with
credentials. All data reaches it only through the injected `window.queries`,
whose JSON has `<` escaped so result data containing `</script>` can't break out
of the prologue.

Like the rest of the app (and [predefined queries](./query.md)), persisted
dashboards are **global**, shared via SQLite, and keyed by name. The agent HTML
is untrusted and confined to the sandbox; it never sees connection secrets or
the session cookie.

## Related docs

- [queryview.md](./queryview.md) — the two pages and the prompt commands.
- [remote.md](./remote.md) — arming a session, the session id, the MCP server.
- [api.md](./api.md) — `/api/runqueries`, `/api/dashboards`, the `dashboard` SSE event.
- [connect.md](./connect.md) — connections and their stored database.
