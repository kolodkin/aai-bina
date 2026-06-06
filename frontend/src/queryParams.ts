import yaml from 'js-yaml'

// A dropdown selector with its choices resolved to a concrete list. The chosen
// value is substituted into the SQL via a `{name}` placeholder. This is what the
// `<select>` render and applyParams consume — for an `options_sql` param the
// list is filled in once the query resolves. See docs/query.md.
export type ParamDef = { name: string; options: string[] }

// A dropdown selector as declared in a predefined query's cell_view YAML under
// the reserved `params:` key. Either `options` (a static list) or `optionsSql`
// (a query whose first column supplies the list) is set, never both.
export type ParamSpec = {
  name: string
  options?: string[]
  optionsSql?: string
}

// Parse YAML text into a plain object, or null on a parse error or a
// non-object/array root. Shared guard for the cell_view YAML, which carries
// both column-render rules and the `params:` selectors.
export function parseYamlObject(
  text: string | null | undefined,
): Record<string, unknown> | null {
  if (!text) return null
  let doc: unknown
  try {
    doc = yaml.load(text)
  } catch {
    return null
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return null
  return doc as Record<string, unknown>
}

// Parse the `params:` section of the cell_view YAML into selector specs.
// Mirrors parseCellViewYaml's defensive contract: a parse error, a missing or
// non-list `params`, or any malformed entry is dropped — a broken config never
// breaks the panel, it just yields no dropdowns. A param may declare a static
// `options` list or an `options_sql` query, but declaring both is a config
// error and drops the entry.
export function parseQueryParams(text: string | null | undefined): ParamSpec[] {
  const doc = parseYamlObject(text)
  if (!doc) return []
  const raw = doc.params
  if (!Array.isArray(raw)) return []
  const out: ParamSpec[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue
    const o = entry as Record<string, unknown>
    if (typeof o.name !== 'string' || o.name === '') continue
    const hasOptions = Array.isArray(o.options)
    const sql = typeof o.options_sql === 'string' ? o.options_sql.trim() : ''
    // Mutually exclusive: declaring both keys is ambiguous, so drop the entry.
    if (hasOptions && sql) continue
    if (sql) {
      out.push({ name: o.name, optionsSql: sql })
      continue
    }
    if (hasOptions) {
      // Keep scalars only; null and nested objects/arrays (all typeof 'object')
      // are not valid option values.
      const options = (o.options as unknown[])
        .filter((v) => typeof v !== 'object')
        .map(String)
      if (options.length === 0) continue
      out.push({ name: o.name, options })
    }
    // Neither a usable options list nor an options_sql query: drop.
  }
  return out
}

// Substitute each `{name}` in the SQL with the selected value as a quoted SQL
// string literal (single quotes doubled). An unselected param falls back to its
// first option; a placeholder with no matching param is left untouched.
export function applyParams(
  sql: string,
  defs: ParamDef[],
  values: Record<string, string>,
): string {
  let out = sql
  for (const def of defs) {
    const value = values[def.name] ?? def.options[0]
    const literal = `'${value.replaceAll("'", "''")}'`
    out = out.replaceAll(`{${def.name}}`, literal)
  }
  return out
}
