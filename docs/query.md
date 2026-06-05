# Querying

Once a database is selected on the active connection, typing `query` opens the
**query panel** below the prompt. The panel runs SQL against the session's
selected database, pages through results, saves/loads reusable queries, and
exports the current page as CSV.

Typing `query` before a database is selected shows the hint
`Select a database first.`

## Panel

```
┌───────────────────────────────────────────────────────────┐
│ [ query ]  [ Predefined queries… ▾ ]            [ Save ]   │
│                                    [Min] [S] [M] [L] [XL]  │
│ ┌───────────────────────────────────────────────────────┐ │
│ │ SELECT …                                              │ │  ← SQL textarea
│ └───────────────────────────────────────────────────────┘ │
│ [Execute] [Fields] Limit [100] Offset [0] [← Prev] [Next →]│
│                                          [Download CSV]    │
│ ┌───────────────────────────────────────────────────────┐ │
│ │ name | …                                              │ │  ← results table
│ │ alpha| …                                              │ │     (scrollable)
│ └───────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

In query mode the command **prompt** moves onto the panel's top row, next to the
predefined-query controls, to save vertical space.

- **SQL textarea** — the query to run. The **Min / S / M / L / XL** toggles change
  its height; **Min** collapses it to nothing to maximize room for results.
- **Execute** — runs the query at the current offset.
- **Fields** — introspects the query's output columns (see
  [Fields, selection & ordering](#fields-selection--ordering)).
- **Limit / Offset** — page size and starting row (defaults `100` / `0`).
- **Previous / Next** — step the offset by ±limit and re-run. Previous is
  disabled at offset `0`. There is no total-row count, so Next can page past the
  last row into an empty result.
- **Download CSV** — downloads the **current page** as `query.csv`, always with
  **all** columns (the field selection below is view-only).
- **Results table** — the rows for the current page, in a scrollable table.

## Fields, selection & ordering

**Fields** asks the backend to describe the current query's output columns —
their names and ClickHouse types — without scanning data (ClickHouse
`DESCRIBE (<query>)`). It populates two pickers from that one list:

- **Select fields** — a toggle per column controlling which columns the results
  table shows. This is **client-side and immediate**: toggling shows/hides the
  column on button press with no re-query, and **Download CSV** ignores it (CSV
  always exports every column). **Select all** / **Clear all** flip every toggle
  at once. New columns from a query edited since the last **Fields** call always
  show, so a stale list can't blank the table.
- **Order by** — pick one or more columns, each **ASC** (default) or **DESC**.
  This is **server-side**, so it only takes effect when the query re-runs: on
  **Execute** / **Previous** / **Next**, on **Download CSV**, or via the **Run**
  button in the order-by section. **Run** re-runs the whole query (same as
  Execute), so it applies the current limit/offset too — not just the ordering.
  Column names are backtick-quoted and directions are whitelisted, so the picker
  can't inject SQL.

Editing the SQL doesn't auto-refresh the pickers — click **Fields** again to
re-describe.

## Pagination

The backend paginates by wrapping the query:

```sql
SELECT * FROM (
<your query>
) LIMIT <limit> OFFSET <offset>
```

So pages are stable only if the query defines its own order — include an
`ORDER BY` for predictable `Previous`/`Next` boundaries.

## Predefined queries

Predefined queries are reusable SQL **shared globally** (not per session),
keyed by **connection type** (e.g. `clickhouse`). They are stored in the
`predefined_queries` SQLite table:

```sql
CREATE TABLE predefined_queries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  query_name TEXT NOT NULL,
  type       TEXT NOT NULL,   -- connection type (clickhouse, …)
  query      TEXT NOT NULL,
  cell_view  TEXT,            -- raw YAML; per-column render config + params (see below)
  UNIQUE (type, query_name)
);
```

- The **selector** lists saved queries for the active connection's type;
  choosing one loads its SQL into the textarea and makes it the active name. Its
  **+ New name…** item prompts for a fresh name (no separate name field).
- **Save** stores the textarea's SQL (and the `cell_view` YAML, see below)
  under the **currently selected name** and refreshes the selector. Saving an
  existing name **upserts** (overwrites) it.

Renaming and deleting predefined queries are not yet supported — see
[future.md](./future.md).

### Cell views

Each predefined query can carry a **`cell_view`** map controlling how result
cells render. It's authored as YAML in a **"Cell view"** modal — opened from
the toolbar button just before the **Min** size toggle — and stored as raw
text on the predefined query. The modal has **Save** (persists + closes) and
**Cancel** (discards edits + closes); clicking the backdrop also cancels. The
map keys are column names; each entry has a `type` and a `value` template, and
`{cell}` is replaced with the cell's raw value:

```yaml
cve_id:
  type: link
  value: https://nvd.nist.gov/vuln/detail/{cell}
severity:
  type: custom
  value: <strong>{cell}</strong>
```

Supported types:

- **`link`** — render the cell as `<a href target="_blank" rel="noopener noreferrer">{cell}</a>`. `{cell}` is URL-encoded into the href; the resolved scheme must be `http`/`https` (anything else falls back to plain text).
- **`custom`** — render the `value` HTML verbatim with `{cell}` substituted in. The cell value is HTML-escaped before substitution (so DB content can't break out), but the template HTML is **trusted** and is not sanitized. **Anyone who can save a predefined query can inject markup/script that runs in every viewer's browser**, because predefined queries are shared globally with no auth.

Both wrappers (the `<a>` for `link`, the `<span>` for `custom`) automatically
carry `data-testid="cell-<columnName>"`, so e2e tests can target rendered cells
without baking testids into the YAML.

Apply timing: rendering uses the **saved** `cell_view` of the **currently
selected** predefined query — edits to the editor only take effect after
**Save** (which re-fetches the list). Ad-hoc SQL with no selected predefined
query renders plain.

A broken (unparseable or unrecognized-shape) `cell_view` is ignored: the table
falls back to plain rendering rather than failing.

### Query parameters

A predefined query can declare **dropdown selectors** whose chosen value is
substituted into the SQL. They live in a reserved **`params:`** key inside the
same `cell_view` YAML (so `params` is never treated as a column-render rule).
Each entry has a `name` and a list of `options`:

```yaml
params:
  - name: source
    options: [a, b, c]
```

With the query:

```sql
select * from events where source = {source}
```

a labelled dropdown appears above the SQL textarea, one per param, populated
with the declared options. The placeholder **`{source}`** is replaced with the
selected value as a **quoted SQL string** (`source = 'b'`), with embedded single
quotes doubled. Write the placeholder where a value goes — no surrounding quotes
of your own. A `{name}` with no matching param is left untouched; a param whose
`{name}` never appears in the query is harmless.

**Changing a dropdown re-runs the query immediately** (resetting to offset 0).
Substitution applies everywhere the query runs — **Execute**, **Previous** /
**Next**, **Fields** (`DESCRIBE`), and **Download CSV** — so all of them see the
currently selected values. The first option is the default.

Because values are constrained to the declared `options` and quoted/escaped on
substitution, params stay within the existing trust model (the SQL textarea is
already sent to the backend as-is). A broken or absent `params:` block simply
renders no dropdowns.

## Results & CSV

Results come back from ClickHouse as `TabSeparatedWithNames` and render as an
HTML table (first row = column names). The table scrolls within the panel; wide
results scroll horizontally. **Download CSV** re-runs the current page asking for
`CSVWithNames` and saves it as `query.csv` — it exports the current page (not the
full result set) and always includes every column, regardless of the **Select
fields** view.

## API

| Method | Path                        | Body                                          | Result |
| ------ | --------------------------- | --------------------------------------------- | ------ |
| POST   | `/api/clickhouse/query`     | `{query, limit?, offset?, format?, order_by?}` | `{ok, output}` (raw text) \| `{ok:false, message}`. `format:"csv"` returns CSV. `order_by` is `[{name, dir}]` (`dir` ASC/DESC). Empty query → `400`; no session → `409`. |
| POST   | `/api/clickhouse/describe`  | `{query}`                                     | `{ok, fields:[{name, type}]}` — the query's output columns, via `DESCRIBE`, no data scanned. \| `{ok:false, message}`. Empty query → `400`; no session / no database → `409`. |
| GET    | `/api/predefined-queries`   | `?type=<connType>`                            | `{queries:[{query_name, query, cell_view}]}` for that connection type. `cell_view` is raw YAML text or `null`. |
| POST   | `/api/predefined-queries`   | `{query_name, type, query, cell_view?}`       | `{ok}`; upserts a predefined query. `cell_view` is optional raw YAML; empty/missing clears it. Missing required fields → `400`. |

Queries run over the ClickHouse HTTP interface (HTTP Basic auth, 5s timeout),
scoped to the session's selected database.

## Related docs

- [queryview.md](./queryview.md) — the single-prompt page concept.
- [connect.md](./connect.md) — connecting, storage, sessions.
- [api.md](./api.md) — the full backend JSON API.
