# Query param dropdowns from a SQL query (`options_sql`)

## Summary

Predefined queries can declare dropdown selectors in their `cell_view` YAML under
`params:`. Today each selector lists its choices as a static array (`options: [a, b, c]`).
This adds an alternative: `options_sql`, a query whose result rows become the dropdown
choices. The chosen value is substituted into the main SQL via the existing `{name}`
placeholder, exactly as static options are today.

The feature is **frontend-driven**: `options_sql` is just more YAML stored verbatim in the
existing `cell_view` column, resolved client-side via the existing `/api/clickhouse/query`
endpoint against the current connection. There are **no backend, API, or database changes**.

## Config format

```yaml
params:
  - name: source              # static (unchanged)
    options: [a, b, c]
  - name: host                # new: choices come from a query
    options_sql: SELECT DISTINCT host FROM system.clusters ORDER BY host
```

Rules:

- A param has **either** `options` **or** `options_sql`, never both. If both keys are
  present the entry is **dropped** (mutually-exclusive validation error), consistent with
  the existing contract that a broken config yields no dropdown rather than breaking the
  panel.
- `options_sql` must be a non-empty string; otherwise the entry is dropped.
- Result mapping: **first column, every row**, stringified, in the query's own order. The
  author is responsible for `DISTINCT` / `ORDER BY` — results are not de-duplicated.

## Component design

### Parsing — `frontend/src/queryParams.ts`

- `ParamDef` becomes `{ name: string; options?: string[]; optionsSql?: string }` with
  exactly one of `options` / `optionsSql` populated.
- `parseQueryParams`:
  - Require a non-empty string `name` (unchanged).
  - If both `options` and `options_sql` are present → skip the entry.
  - Else if `options` is a valid scalar array → `{ name, options }` (existing logic).
  - Else if `options_sql` is a non-empty string → `{ name, optionsSql }`.
  - Else skip.
- `applyParams` and the `<select>` rendering are **unchanged**: they only ever consume a
  *resolved* def whose `options` is a concrete array (see data flow). For a resolved def the
  default value remains `def.options[0]`.

### Data flow — `frontend/src/QueryView.tsx`

New state:

- `sqlOptions: Record<string, string[]>` — resolved option arrays keyed by param name.
- `optionsError: string | null` — message when an `options_sql` query fails or is empty.

Fetch effect (keyed on `[paramDefs, connectionType]`, so it runs once when a query loads
and again only on selecting a different query or a Save — i.e. "once when the query
loads", with results cached for the session):

- Collect params that have `optionsSql`. `Promise.all` a POST per param to
  `/api/clickhouse/query` with `{ query: def.optionsSql, format: 'text' }`.
- On success, parse with the existing `parseTsv` and take `rows.map((r) => r[0])`, dropping
  a trailing empty line. Store under the param name in `sqlOptions`.
- Guard against races: if `paramDefs` changed while a fetch was in flight, ignore the stale
  results.

Resolved defs (a `useMemo`) normalize every def to a concrete array so render and
`applyParams` stay untouched:

```ts
const resolvedDefs = useMemo<ParamDef[]>(
  () => paramDefs.map((d) =>
    d.optionsSql ? { name: d.name, options: sqlOptions[d.name] ?? [] } : d),
  [paramDefs, sqlOptions],
)
```

Every downstream use of `paramDefs` (the `<select>` map, the seed-defaults effect, and all
`applyParams(sql, …)` call sites) switches to `resolvedDefs`.

### Error handling & blocking the main query

An unresolved `options_sql` **blocks** the main query rather than degrading silently:

- Derived `optionsReady` = every `optionsSql` param has a resolved array with ≥1 entry and
  `optionsError` is null.
- If any options query **errors or returns zero rows**: set `optionsError` to that message,
  surface it through the existing `error` banner prefixed with the param name (e.g.
  `options for "host": <message>` or `options for "host": query returned no rows`), and do
  not auto-run.
- While options are loading or `!optionsReady`: the Run / describe / CSV buttons are
  disabled, and the dropdown-change and `pushed`-query run paths bail early, so the main
  query can never fire with an unfilled `{host}` placeholder.
- Once options resolve, defaults seed to the first option and the panel behaves exactly as
  today.

## Testing (TDD)

Match the style of the existing `queryParams` unit tests and `QueryView` component tests.

queryParams:

- `options_sql` parses to `{ name, optionsSql }`.
- Both `options` and `options_sql` present → entry dropped.
- `options_sql` empty / non-string → entry dropped.
- Static `options` behavior unchanged.

QueryView (mocked `fetch`):

- Options query fires on load and populates the `<select>`.
- First row becomes the default and is substituted into the main query.
- An erroring options query blocks the run and shows the banner.
- A zero-row options result blocks with the "no rows" message.
- A static-`options` param still works with no extra fetch.

No backend changes, so no Python test changes.

## Out of scope (YAGNI)

- Two-column value/label mapping (first column only).
- Manual refresh / re-fetch on dropdown open (load-once only).
- Static `options` as a fallback when `options_sql` fails (the two keys are mutually
  exclusive).
- Dependent dropdowns (an `options_sql` referencing another param's value).
