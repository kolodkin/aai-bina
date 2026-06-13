# Default views for Array / Map / Tuple

## Goal

ClickHouse complex types arrive in the `TabSeparatedWithNames` result payload as
raw serialized strings and render verbatim in a monospace cell:

- `Array(String)` → `['a','b','c']`
- `Map(String, UInt64)` → `{'x':1,'y':2}`
- `Tuple(id Int32, name String)` → `(1,'a')`

Give these types a **built-in default view** — a readable, structured rendering
that requires no `cell_view` YAML. This extends the existing per-column cell-view
system (see `2026-05-28-cell-view-design.md` and `docs/query.md`) with renderers
keyed by the column's ClickHouse **type** rather than by author-supplied config.

## Decisions

Settled during brainstorming:

1. **Interaction model** — inline structured rendering as **plain vertical
   lists**. When a collection has more than 3 items it starts **collapsed**,
   showing the first 3 with an expander; expanding reveals the rest plus a
   collapse control.
2. **Type source** — every **Execute** auto-runs `DESCRIBE (<query>)` so exact
   ClickHouse types are known. Keying off the real type (not a value-shape
   heuristic) distinguishes a genuine `Array` from a `String` that merely starts
   with `[`, and supplies named-tuple element names that the value alone lacks.
3. **Style** — plain vertical lists (not chips/pills), matching the monospace
   table.
4. **Nesting** — **top-level only**, with two explicit exceptions:
   `Array(Tuple(...))` and `Array(Map(...))` render each element with its inner
   (tuple / map) view. Any deeper nesting renders the nested piece as its raw
   serialized substring.
5. **Precedence** — an explicit `cell_view` entry for a column wins (and is the
   opt-out); otherwise a complex type gets the default view; otherwise raw text.

## Rendering

All renderings are **plain vertical lists** in the existing monospace cell.
`PREVIEW_COUNT = 3`: a collection with more than 3 items starts collapsed,
showing the first 3 followed by an expander (`… (+N more) ▸`); expanded shows
every item plus a collapse control (`▾ collapse`). The expander/collapse control
is a real `<button>` carrying a `data-testid` so e2e can drive it. Cells start
**collapsed**; per-cell expanded state is component-local, so paging resets it.

- **Array(scalar)** — one element per line, scalar strings unquoted.
- **Map** — one `key → value` per line.
- **Tuple** — one `name: value` per line. Names come from the type for named
  tuples (`Tuple(id Int32, name String)` → `id: 1`); unnamed tuples use the
  positional index (`0: 1`, `1: a`). Fixed arity, but the same `> 3` collapse
  rule applies.
- **Array(Tuple(...))** — vertical list of elements, **each element rendered as
  its tuple view** (its own `name: value` lines, named from the inner tuple
  type). The outer array collapses to the first 3 tuples.
- **Array(Map(...))** — vertical list of elements, **each element rendered as its
  map view** (`key → value` lines). The outer array collapses to the first 3
  maps.
- **Deeper nesting** — any complex piece below the levels above (e.g. an
  `Array(Array(...))` element, a complex `Tuple` field, an `Array`-valued `Map`
  entry) renders as its **raw serialized substring**.

Values are rendered as plain text nodes (React-escaped), so DB content cannot
inject markup — no `dangerouslySetInnerHTML` is involved.

## Type & value parsing

Two pure helpers in a new **`frontend/src/complexCell.ts`** (no React; mirrors
the existing `cellView.ts`/`CellViewModal.tsx` split). Both rely on one
**nesting-aware splitter** that splits a string on top-level commas (and, for
maps, the top-level `key:value` colon) while respecting `[] () {}` nesting and
single-quoted strings with backslash escapes.

**Type descriptor** — parse a ClickHouse type string into a small descriptor:

```ts
type ComplexType =
  | { kind: 'array'; element: ComplexType | null }   // element only set for tuple/map
  | { kind: 'map' }
  | { kind: 'tuple'; fields: { name: string | null }[] }
  | null                                              // scalar / unsupported
```

- Recognizes the outer constructor `Array(...)` / `Map(...)` / `Tuple(...)`.
- For `Array`, looks one level in: `Array(Tuple(...))` and `Array(Map(...))`
  capture the inner descriptor; any other array element kind is treated as
  scalar (raw substring per element).
- For `Tuple`, splits fields with the nesting-aware splitter. A field is
  **named** when it has a top-level space separating a leading identifier from a
  type expression (heuristic: `^[A-Za-z_]\w*\s+` where the following token begins
  a type). Otherwise the field is positional. A wrong guess falls back to
  positional, which is safe.
- Wraps such as `Nullable(...)` / `LowCardinality(...)` are unwrapped before
  classifying so e.g. `Array(Nullable(String))` is still a scalar array.

**Value parser** — parse a serialized value into structured substrings:

- `parseArray(s)` → `string[]` of element substrings (`['a','b']` → `["'a'","'b'"]`).
- `parseTuple(s)` → `string[]` of field substrings (`(1,'a')` → `["1","'a'"]`).
- `parseMap(s)` → `[string, string][]` of key/value substrings.
- `unquoteScalar(s)` → strip surrounding single quotes and unescape (`\\`, `\'`,
  `\n`, `\t`, …); non-quoted scalars (numbers, etc.) pass through unchanged.

The naive `parseTsv` split on `\t`/`\n` is unaffected: ClickHouse escapes any
tab/newline inside a string element at the TSV layer (`\t`, `\n`), so a complex
value always occupies a single field with no literal control characters.

## Component

**`frontend/src/ComplexCell.tsx`** — `<ComplexCell type={ComplexType} raw={string}
col={string} />`:

- Holds the `expanded` boolean (default `false`).
- Renders the appropriate vertical list per the type descriptor, applying the
  `PREVIEW_COUNT` collapse rule and the expand/collapse button.
- For `Array(Tuple)`/`Array(Map)`, renders each shown element via the inner
  descriptor (a tuple sub-list / map sub-list).
- Carries `data-testid="cell-<col>"` (consistent with existing cell views) and a
  `data-testid` on the expand/collapse button.

## Wiring (`frontend/src/QueryView.tsx`)

- **Auto-describe.** `execute()` fires `DESCRIBE (<query>)` alongside the query.
  Results render immediately and do **not** await describe; when describe
  resolves it populates a `colTypes: Record<string, string>` and the table
  re-renders with default views. A describe failure is swallowed (cells stay
  raw). The describe result is **cached by SQL text**, so Prev/Next (same SQL)
  skip re-describing; only a changed query re-describes. This path is independent
  of the **Fields** button — auto-describe feeds rendering only and never resets
  `visibleCols`.
- **`renderCell`** gains the column type and applies precedence: explicit
  `appliedViews[col]` (`link`/`custom`) first; else, if `parseComplexType(colTypes[col])`
  is non-null, return `<ComplexCell>`; else raw text. The type descriptor is
  memoized per column.

## Out of scope (YAGNI)

- Recursion deeper than the `Array(Tuple)` / `Array(Map)` cases.
- Chips/pills, color coding, or any non-plain-list styling.
- A configurable preview count or per-column toggle of the default view (the
  escape hatch is an explicit `cell_view` entry).
- Applying default views to ad-hoc results when describe fails or is
  unsupported (they stay raw).
- Default views in the CSV export (CSV stays the raw serialized value).

## Testing

- **Unit (`frontend/src/complexCell.test.ts`):**
  - Type parsing: `Array(String)`, `Map(String, UInt64)`, named and unnamed
    `Tuple`, `Array(Tuple(...))`, `Array(Map(...))`, `Array(Nullable(String))`,
    and a non-complex type → `null`.
  - Value parsing: arrays/tuples/maps including quoted strings containing commas
    and escaped quotes; `unquoteScalar` unescaping.
  - Collapse threshold: ≤3 vs >3 items.
- **e2e (`e2e/`, Playwright):** seed a predefined query returning an `Array`, a
  `Map`, a `Tuple`, an `Array(Tuple)`, and an `Array(Map)` column; execute;
  assert each cell renders a vertical list, that a >3-item cell starts collapsed
  showing 3 items, and that the expander reveals the rest and collapses again.

## Docs to update

- `docs/query.md` — a **"Default views for complex types"** subsection under
  Cell views: the supported types, the plain-vertical-list rendering, the first-3
  collapse, the `Array(Tuple)`/`Array(Map)` inner-view behavior, top-level-only
  nesting, the explicit-`cell_view` precedence/opt-out, and the auto-`DESCRIBE`
  behavior on Execute.
