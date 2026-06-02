# Dashboards (agent-authored, pushed to a live session)

A **dashboard** is an HTML layout plus a set of named SQL queries. An AI agent
authors one and pushes it into a live, armed QueryView browser session over MCP;
the dashboard is also persisted so it can be reopened later by name.

The split of responsibilities mirrors the [remote-control](./remote.md) layer:
the agent never receives query results back. The **browser** runs the queries
(it is the trusted code with a session) against a named connection and feeds the
results into the agent-authored HTML, which renders inside an isolated iframe.

## The page (`/dashboard`)

`/dashboard` is the second top-level page (the first is the query workflow at
`/queries`; see [queryview.md](./queryview.md)). It has:

- a **dropdown** of saved dashboards — selecting one sets `?name=<name>`, so the
  URL is shareable and the back button works;
- a **sandboxed iframe** that renders the selected dashboard.

Open it from the prompt with `dashboard` (just the dropdown) or
`dashboard <name>` (jump straight to one), from the corner nav, or by URL
(`/dashboard?name=<name>`). Reopening re-fetches the dashboard from the store
and re-runs its queries, so a reloaded or shared link always shows live data.

## The `upsert_dashboard` MCP tool

The FastMCP server at `/mcp` (see [remote.md](./remote.md) for arming and the
session id) exposes:

- `upsert_dashboard(session_id, name, connection, html, queries)` — persist the
  dashboard by `name` (upsert) **and** push it to the browser identified by
  `session_id`, which navigates to `/dashboard?name=<name>` and renders it.
  Returns `{ok, persisted, pushed, message}`. An unknown/disarmed `session_id`
  still persists the dashboard (`persisted:true`) while reporting `pushed:false`
  — it stays openable by name.

`session_id` and `connection` are distinct: `session_id` is the live browser to
push the preview to; `connection` is the saved [connection](./connect.md) the
queries run against (by name — a dashboard is self-contained and portable). The
connection's **stored database** is used, so select a database for that
connection first, or fully-qualify table names as `db.table`.

The REST mirror `POST /api/dashboards` takes the same fields (with an optional
`session_id`) and drives the same persist-and-push path — the e2e suite uses it.
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
`window.queries.sales.revenue` is the list of values in that column. Column
order is preserved. Load any chart library you like from a CDN inside the HTML.

A minimal dashboard:

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

### Running the queries: `/api/runqueries`

The browser POSTs `{connection, queries}` to `/api/runqueries`, which runs each
query against the connection's stored database (wrapped in a paginated subselect
capped at 1000 rows) and returns the column-oriented results.

It is **fail-fast**: if any query fails, the connection is unknown, or it has no
selected database, the whole request returns an HTTP error and the page shows a
dashboard-level error banner instead of rendering partial panels. On success the
results are always complete — every named query is present in `window.queries`.
A failing query's message is prefixed with its panel name (e.g.
`churn: Unknown table …`) so it's clear which one to fix.

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
