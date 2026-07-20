// Dashboard-level selectors, declared in a dashboard's `params` and substituted
// into its queries via `{name}` placeholders — the same vocabulary predefined
// queries use (see queryParams.ts), extended for what a dashboard needs:
//
//   value       a literal: {region} -> 'eu'
//   identifier  a table/column: {field} -> `city` (a literal would make
//               `SELECT {field}` select a constant string, not the column)
//   dimension   an optional GROUP BY column: checked -> its own column, else ''
//
// An options_sql may itself reference another param ({table}), so field lists
// can depend on the selected table; resolveOrder sequences those.

export type ParamKind = 'value' | 'identifier' | 'dimension'

// A selector with its option list resolved and a value chosen (or left empty).
export type ResolvedParam = {
  name: string
  kind: string
  options: string[]
  value: string
}

// Runs an options_sql and hands back its column-oriented result.
export type OptionsRunner = (
  queries: Record<string, string>,
) => Promise<
  { ok: true; results: Record<string, Record<string, unknown[]>> } | { ok: false; message: string }
>

export type DashboardParam = {
  name: string
  kind: ParamKind
  options?: string[]
  optionsSql?: string
  // Whether the first option is chosen for you. `default: none` leaves the
  // selector empty so nothing runs until a choice is made — useful when the
  // first option is an arbitrary pick rather than a sensible default.
  autoSelect: boolean
}

const KINDS: ParamKind[] = ['value', 'identifier', 'dimension']
const CASTS = ['literal', 'identifier']
// `{name}` substitutes per the param's kind; `{name:literal}` / `{name:identifier}`
// override it for that one spot. A table param is an identifier in `FROM {table}`
// but a string in `WHERE table = {table:literal}`.
const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)(?::([A-Za-z_]+))?\}/g

// Parse the `params` list. Defensive, like parseQueryParams: a malformed entry
// is dropped so a broken declaration costs one selector, not the dashboard.
export function parseDashboardParams(raw: unknown): DashboardParam[] {
  if (!Array.isArray(raw)) return []
  const out: DashboardParam[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue
    const o = entry as Record<string, unknown>
    if (typeof o.name !== 'string' || o.name === '') continue

    const kind = (o.kind ?? 'value') as ParamKind
    if (!KINDS.includes(kind)) continue

    const autoSelect = o.default !== 'none'
    const hasOptions = Array.isArray(o.options)
    const sql = typeof o.options_sql === 'string' ? o.options_sql.trim() : ''
    if (hasOptions && sql) continue // ambiguous

    if (sql) {
      out.push({ name: o.name, kind, optionsSql: sql, autoSelect })
      continue
    }
    if (hasOptions) {
      const options = (o.options as unknown[]).filter((v) => typeof v !== 'object').map(String)
      if (options.length === 0) continue
      out.push({ name: o.name, kind, options, autoSelect })
      continue
    }
    // A dimension is a checkbox: it needs no option list.
    if (kind === 'dimension') out.push({ name: o.name, kind, autoSelect })
  }
  return out
}

// The `{name}` placeholders a query references, in order of first appearance.
export function placeholdersIn(sql: string): string[] {
  const seen = new Set<string>()
  for (const m of sql.matchAll(PLACEHOLDER)) seen.add(m[1])
  return [...seen]
}

function quoteLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`
}

// ClickHouse backtick-quoting. A backtick in the value can't be trusted through
// string assembly, and no real table or column carries one, so reject it.
function quoteIdentifier(name: string, param: string): string {
  if (name.includes('`')) throw new Error(`invalid identifier for {${param}}: ${name}`)
  return `\`${name}\``
}

// Replace every declared `{name}`; placeholders with no matching param are left
// alone (they may be literal braces in the SQL).
export function substituteParams(
  sql: string,
  params: DashboardParam[],
  values: Record<string, string>,
): string {
  const byName = new Map(params.map((p) => [p.name, p]))
  return sql.replace(PLACEHOLDER, (token, name: string, cast?: string) => {
    const p = byName.get(name)
    if (!p) return token
    if (cast && !CASTS.includes(cast)) throw new Error(`unknown cast for {${name}}: ${cast}`)

    const raw = values[name]
    if (p.kind === 'dimension') {
      // Unchecked: substitute an empty literal so the query keeps its shape and
      // the column simply contributes nothing to the grouping.
      return raw ? quoteIdentifier(name, name) : "''"
    }
    if (raw === undefined || raw === '') throw new Error(`no value selected for {${name}}`)
    const asIdentifier = cast ? cast === 'identifier' : p.kind === 'identifier'
    return asIdentifier ? quoteIdentifier(raw, name) : quoteLiteral(raw)
  })
}

// Declaration order, except a param whose options_sql references another is
// resolved after it. Throws on a cycle rather than spinning.
export function resolveOrder(params: DashboardParam[]): DashboardParam[] {
  const byName = new Map(params.map((p) => [p.name, p]))
  const ordered: DashboardParam[] = []
  const done = new Set<string>()
  const visiting = new Set<string>()

  function visit(p: DashboardParam) {
    if (done.has(p.name)) return
    if (visiting.has(p.name)) throw new Error(`params dependency cycle at ${p.name}`)
    visiting.add(p.name)
    for (const dep of placeholdersIn(p.optionsSql ?? '')) {
      const target = byName.get(dep)
      if (target) visit(target)
    }
    visiting.delete(p.name)
    done.add(p.name)
    ordered.push(p)
  }

  for (const p of params) visit(p)
  return ordered
}

// The declared params a query still needs a value for. Dimensions never appear:
// an unchecked one is off, not unset. Placeholders matching no param are the
// query's own business.
export function missingParams(
  sql: string,
  params: DashboardParam[],
  values: Record<string, string>,
): string[] {
  const byName = new Map(params.map((p) => [p.name, p]))
  return placeholdersIn(sql).filter((name) => {
    const p = byName.get(name)
    return !!p && p.kind !== 'dimension' && !values[name]
  })
}

// Resolve every selector's options in dependency order, keeping the caller's
// values where they are still offered. A param whose options_sql depends on a
// param that isn't chosen yet resolves to no options and no value — with
// `default: none` that is the normal opening state, not a failure.
export async function resolveParams(
  params: DashboardParam[],
  values: Record<string, string>,
  run: OptionsRunner,
): Promise<ResolvedParam[]> {
  const resolved: ResolvedParam[] = []
  const chosen: Record<string, string> = {}

  for (const p of resolveOrder(params)) {
    let options = p.options ?? []
    if (p.optionsSql) {
      if (missingParams(p.optionsSql, params, chosen).length) {
        chosen[p.name] = ''
        resolved.push({ name: p.name, kind: p.kind, options: [], value: '' })
        continue
      }
      const r = await run({ o: substituteParams(p.optionsSql, params, chosen) })
      if (!r.ok) throw new Error(r.message)
      const firstColumn = Object.values(r.results.o ?? {})[0] ?? []
      options = firstColumn.map(String)
    }
    // A dimension is a checkbox: its value is the toggle, not a choice.
    const want = values[p.name]
    const value =
      p.kind === 'dimension'
        ? (want ?? '')
        : want && options.includes(want)
          ? want
          : p.autoSelect
            ? (options[0] ?? '')
            : ''
    chosen[p.name] = value
    resolved.push({ name: p.name, kind: p.kind, options, value })
  }
  return resolved
}
