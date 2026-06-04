# Query parameter dropdowns — design

## Summary

Let a predefined query declare **custom dropdown selectors** whose chosen value
is substituted into the SQL via a `{name}` placeholder. The selectors are
defined in a new reserved `params:` section inside the existing `cell_view`
YAML, so no new DB column, API field, or migration is required. Substitution
happens client-side, immediately before the SQL is sent — consistent with the
existing model, where the entire query textarea is already sent raw to the
backend.

Example config (authored in the existing Cell view modal):

```yaml
params:
  - name: source
    options: [a, b, c]
cve_id:                 # existing column-render config, unchanged
  type: link
  value: https://nvd.nist.gov/vuln/detail/{cell}
```

With query `select * from a where source = {source}`, picking `b` from the
generated dropdown runs `select * from a where source = 'b'`.

## Decisions (from brainstorming)

- **Config location:** reserved `params:` key inside the existing `cell_view`
  YAML — one config editor, one DB column.
- **SQL storage:** unchanged. The SQL stays in its own `query` column; it is
  *not* folded into the YAML. (Considered and explicitly rejected.)
- **Substitution:** auto-quote as a string. `{name}` → `'value'`, with embedded
  single quotes doubled (`'` → `''`).
- **Re-run trigger:** changing a dropdown value re-runs the query immediately,
  resetting to offset 0.
- **Scope:** frontend (`QueryView.tsx`) + docs + one e2e test. No backend, API,
  or DB changes.

## Architecture

All work is in `frontend/src/QueryView.tsx`, mirroring the existing
`parseCellViewYaml` / `renderCell` helpers and their defensive "a broken config
never breaks the panel" philosophy.

### Data flow

```
savedCellView (YAML string, from selected PredefinedQuery.cell_view)
  │
  ├─ parseCellViewYaml(...)  → CellViewMap        (existing; now skips `params`)
  └─ parseQueryParams(...)   → ParamDef[]         (new)
        │
        ▼
   paramDefs (useMemo)  ──►  render <select> dropdowns
        │                         │ onChange
        ▼                         ▼
   paramValues (state, defaults to each param's first option)
        │
        ▼
   runWith / describe / downloadCsv
        └─ applyParams(sql, paramDefs, values) → resolved SQL → backend
```

### New units

**`type ParamDef = { name: string; options: string[] }`**

**`parseQueryParams(text: string | null | undefined): ParamDef[]`**
- Parses the YAML; on parse error or non-object root, returns `[]`.
- Reads the top-level `params` key. If absent or not an array, returns `[]`.
- For each array entry, keeps it only if it is an object with a non-empty
  string `name` and an `options` array containing at least one scalar
  (string/number/boolean, each coerced via `String(...)`). Malformed entries
  are dropped. Never throws.

**`applyParams(sql: string, defs: ParamDef[], values: Record<string,string>): string`**
- For each def, resolves `value = values[name] ?? options[0]`, builds the SQL
  literal `'` + `value.replaceAll("'", "''")` + `'`, and replaces every
  occurrence of `{name}` in the SQL with that literal (`String.replaceAll`,
  literal match — not regex).
- A `{name}` with no matching def is left untouched; a def whose `{name}` never
  appears in the SQL is a harmless no-op.

**`parseCellViewYaml` change:** skip the reserved key `params` so it is never
interpreted as a column-render rule. (It already happens to be dropped because
its value is an array, but skipping it explicitly documents the reservation.)

### State & effects

- `paramDefs = useMemo(() => parseQueryParams(savedCellView), [savedCellView])`.
  `savedCellView` is the already-existing single source of truth (the selected
  predefined query's `cell_view`), so params reload automatically when a
  different predefined query is selected or after Save.
- `const [paramValues, setParamValues] = useState<Record<string,string>>({})`.
- An effect keyed on `paramDefs` resets `paramValues` to each param's first
  option, **preserving** any current selection that is still a valid option for
  that param (so re-renders don't clobber a user's pick, but a config change
  re-seeds defaults).

### UI

A dropdown row rendered only when `paramDefs.length > 0`, placed directly below
the predefined-query selector row and above the size-toggle row. Each param
renders a label + native `<select>` styled with the existing `.glass-input`
class:

```
[ Predefined queries… ▾ ]                         [ Save ]
source: [ a ▾ ]   region: [ us ▾ ]                         ← new params row
                                   [Min] [S] [M] [L] [XL]
SELECT … where source = {source}
```

- `data-testid="param-select"`, `data-param="{name}"` on each `<select>`, one
  `<option>` per declared option.
- **onChange:** compute `next = { ...paramValues, [name]: value }`, call
  `setParamValues(next)`, then re-run at offset 0. Because React state is async,
  the run must use the new values directly rather than the not-yet-committed
  state — the same pattern the pushed-query effect already uses.

### Substitution wiring

- `runWith` gains an optional `paramOverride?: Record<string,string>` argument.
  Internally it sends `applyParams(q, paramDefs, paramOverride ?? paramValues)`
  as the `query`. This centralizes resolution so Execute, Previous/Next (which
  call `run` → `runWith`), pushed queries, and the dropdown's auto-run all get
  the substituted SQL. The dropdown onChange passes its `next` map as the
  override to avoid the stale-state read.
- `describe` and `downloadCsv` build their own request bodies, so each applies
  `applyParams(sql, paramDefs, paramValues)` to its `query`/`sql` before sending
  (no concurrent value change there, so reading state is fine).

## Error handling

- Malformed or absent `params` YAML → `parseQueryParams` returns `[]` → no
  dropdown row, panel behaves exactly as today. A broken config never blanks the
  panel (same guarantee as `parseCellViewYaml`).
- Unmatched placeholders and unused params are no-ops (see `applyParams`).
- Values are constrained to the declared `options`, and single quotes are
  doubled, so substitution stays within the existing trust model (the textarea
  is already sent raw; a fixed option whitelist is strictly more constrained).

## Testing

No frontend JS unit-test runner exists in this repo; the established pattern is
Python + Playwright e2e (`e2e/test_query.py`) against the seeded ClickHouse
`test` db (`items` with `id, name` = alpha/beta/gamma). This feature is covered
there rather than by introducing a new test framework.

New e2e test `test_query_param_dropdown_substitutes_value` (in
`e2e/test_query.py`, using the existing `_open_query_panel` helper):

1. Fill SQL `SELECT name FROM items WHERE name = {sel} ORDER BY id`.
2. Name the query via the predefined-select `::new::` prompt.
3. Open the Cell view modal, author:
   ```yaml
   params:
     - name: sel
       options: [alpha, beta, gamma]
   ```
   Save (closes modal, persists `cell_view` + SQL).
4. Assert the params row renders a `param-select` with `data-param="sel"` and
   options alpha/beta/gamma; default selection `alpha` auto-runs to show only
   `alpha`.
5. Select `beta`; assert the query auto-re-runs and the table shows `beta` and
   not `alpha`/`gamma` — proving the value was substituted **and quoted**
   correctly (an unquoted `beta` would be a ClickHouse error).
6. `shot(...)` at the key steps, matching the suite's screenshot convention.

The `parseQueryParams` / `applyParams` quoting logic is exercised end-to-end by
step 5 (a quoting bug surfaces as a query error or wrong rows). Adding a JS unit
runner is out of scope.

## Docs

Update `docs/query.md`: add a short **Query parameters** subsection documenting
the `params:` block, the `{name}` placeholder, auto-quoting, and the
auto-re-run-on-change behavior; note that `params` is a reserved key in the
cell_view YAML.

## Out of scope (YAGNI)

- Free-text / multi-select params, per-param types, numeric/identifier
  (unquoted) substitution.
- Dynamic option lists sourced from a query.
- Moving the SQL into the YAML / schema changes.
```
