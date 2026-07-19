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

export type DashboardParam = {
  name: string
  kind: ParamKind
  options?: string[]
  optionsSql?: string
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

    const hasOptions = Array.isArray(o.options)
    const sql = typeof o.options_sql === 'string' ? o.options_sql.trim() : ''
    if (hasOptions && sql) continue // ambiguous

    if (sql) {
      out.push({ name: o.name, kind, optionsSql: sql })
      continue
    }
    if (hasOptions) {
      const options = (o.options as unknown[]).filter((v) => typeof v !== 'object').map(String)
      if (options.length === 0) continue
      out.push({ name: o.name, kind, options })
      continue
    }
    // A dimension is a checkbox: it needs no option list.
    if (kind === 'dimension') out.push({ name: o.name, kind })
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
