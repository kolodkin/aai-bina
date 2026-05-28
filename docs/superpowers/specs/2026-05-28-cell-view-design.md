# Per-column cell views

## Goal

Let a predefined query control how its result cells render, so a raw value
becomes a richer presentation. The motivating case: a `cve_id` column whose
value `CVE-2021-44228` renders as a link to
`https://nvd.nist.gov/vuln/detail/CVE-2021-44228`.

Configuration lives on the predefined query as a column → view map, authored as
YAML:

```yaml
cve_id:
  type: link
  value: https://nvd.nist.gov/vuln/detail/{cell}
severity:
  type: custom
  value: <h1>{cell}</h1>
```

`{cell}` is the placeholder for the cell's raw value. Rendering is purely
client-side; it never changes the SQL or re-queries.

## Decisions

Settled during brainstorming:

1. **Scope** — views attach **per predefined query**, as a `cell_view` column on
   the `predefined_queries` row. They apply only when that saved query is the
   active one, matched by column name. Ad-hoc SQL (no selected predefined query)
   renders plain.
2. **View types** — `link` and `custom` (an HTML template).
3. **Authoring format** — raw **YAML** in a textarea, parsed client-side with
   `js-yaml`. The DB stores the raw YAML text verbatim; the backend never parses
   it.
4. **Apply timing** — rendering uses the **saved** `cell_view` of the active
   predefined query. Editor edits take effect only after **Save** (which
   re-fetches the list). The saved value is the single source of truth.
5. **Security posture** — `custom`: the cell value is HTML-escaped (so DB data
   is inert) but the template HTML is **trusted** and rendered verbatim.
   `link`: the resolved href is restricted to `http`/`https`.
6. **Naming** — the column / API field / editor are all `cell_view`.
7. **Placeholder** — `{cell}`; every occurrence is substituted.

## Data model

Add one nullable column to `PredefinedQuery` (`backend/queryview/queries.py`):

- `cell_view: str | None` — raw YAML text. `NULL` = no custom views.

The `predefined_queries` table already exists in users' SQLite files and
`SQLModel.metadata.create_all` does not alter existing tables. So schema setup
gains a small idempotent migration: read `PRAGMA table_info(predefined_queries)`
and, if `cell_view` is absent, run
`ALTER TABLE predefined_queries ADD COLUMN cell_view TEXT`. This runs alongside
the existing `_ensure_schema` path. No backend YAML dependency is added.

## API

`backend/queryview/main.py` + `backend/queryview/queries.py`:

- `GET /api/predefined-queries?type=<connType>` → each item gains `cell_view`
  (string or `null`):
  `{queries: [{query_name, query, cell_view}]}`.
- `POST /api/predefined-queries` → accepts an optional `cell_view` string,
  stored as-is. Missing/`null` clears it. Existing required-field validation
  (`query_name`, `type`, `query`) is unchanged. The backend does not validate
  the YAML; an unparseable string simply renders plain client-side.

`list_predefined_queries` and `save_predefined_query` are extended to read/write
the new column.

## Frontend

`frontend/src/App.tsx` (`QueryPanel`) and `frontend/package.json`.

**Dependencies:** add `js-yaml` and `@types/js-yaml`.

**Types:** `PredefinedQuery` gains `cell_view: string | null`. A
`CellView = { type: string; value: string }` shape; the parsed map is
`Record<string, CellView>`.

**Authoring UI:** a collapsible **"Cell view (YAML)"** `<textarea>` near the
Save controls. Selecting a predefined query loads its saved `cell_view` into
this editor (empty when none). **Save** sends `cell_view` (the editor's text)
alongside `query_name` / `type` / `query`; after a successful save the existing
`loadPredefined()` refresh pulls the new saved value back.

**Applied views (rendering source of truth):** derived from the *selected
predefined query's saved* `cell_view` — i.e. `predefined.find(p =>
p.query_name === selectedName)?.cell_view` — parsed with `js-yaml` and memoized.
A parse error or no selection yields an empty map (plain rendering). This is
independent of the editor's current text, so unsaved edits don't affect the
table.

**Cell rendering** (the `<td>` at `App.tsx:1026`): for each cell, look up
`appliedViews[columnName]`:

- **`link`** — substitute `encodeURIComponent(cell)` for every `{cell}` in
  `value` to build the href. If the resolved scheme is not `http`/`https`, fall
  back to plain text. Otherwise render
  `<a href={href} target="_blank" rel="noopener noreferrer">{cell}</a>` (React
  escapes the visible text).
- **`custom`** — HTML-escape `cell`, substitute it for every `{cell}` in
  `value`, render the result with `dangerouslySetInnerHTML`.
- **no entry / unknown type / parse failure** — plain text (current behavior).

A small helper module isolates the substitution + escaping so it is unit-style
testable and the `<td>` stays readable.

## Security

- The `custom` type renders trusted, author-controlled template HTML verbatim.
  The cell value is HTML-escaped before substitution, so untrusted DB content
  cannot break out of the template. The trust assumption — **anyone who can save
  a predefined query can inject markup/script into every viewer's browser**,
  because predefined queries are shared globally with no auth — is documented in
  `docs/query.md`.
- The `link` type rejects any resolved href whose scheme is not `http`/`https`
  (no `javascript:`, `data:`, etc.), falling back to plain text.

## Out of scope (YAGNI)

- Other view types (badge/color, copy button, monospace).
- Placeholders other than `{cell}` (e.g. referencing other columns).
- Global / per-connection-type views, or per-query overrides of a global
  default.
- A structured (non-YAML) per-column editor or YAML syntax highlighting.
- HTML sanitization (DOMPurify) for `custom` — explicitly declined in favor of
  the trusted-template posture.

## Testing

- **Backend** (`backend/tests/`): saving a predefined query with `cell_view`
  and listing it round-trips the value; saving without it leaves `NULL`; the
  `ALTER TABLE` migration is idempotent on a pre-existing table.
- **e2e** (`e2e/`, Playwright): seed a predefined query whose `cell_view`
  defines both a `link` and a `custom` view, select and run it, then assert the
  results table renders an `<a>` with the expected `href` and that the custom
  tag appears.

## Docs to update

- `docs/query.md` — predefined-queries section: the `cell_view` YAML format,
  the two types, the `{cell}` placeholder, apply-after-save behavior, and the
  `custom` trust/security note. Update the predefined-queries API rows.
- `docs/api.md` — `cell_view` on both predefined-query endpoints.
