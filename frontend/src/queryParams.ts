import yaml from 'js-yaml'

// A dropdown selector declared in a predefined query's cell_view YAML under the
// reserved `params:` key. The chosen value is substituted into the SQL via a
// `{name}` placeholder. See docs/query.md.
export type ParamDef = { name: string; options: string[] }

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

// Parse the `params:` section of the cell_view YAML into selector definitions.
// Mirrors parseCellViewYaml's defensive contract: a parse error, a missing or
// non-list `params`, or any malformed entry is dropped — a broken config never
// breaks the panel, it just yields no dropdowns.
export function parseQueryParams(text: string | null | undefined): ParamDef[] {
  const doc = parseYamlObject(text)
  if (!doc) return []
  const raw = doc.params
  if (!Array.isArray(raw)) return []
  const out: ParamDef[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue
    const o = entry as Record<string, unknown>
    if (typeof o.name !== 'string' || o.name === '') continue
    if (!Array.isArray(o.options)) continue
    // Keep scalars only; null and nested objects/arrays (all typeof 'object')
    // are not valid option values.
    const options = o.options.filter((v) => typeof v !== 'object').map(String)
    if (options.length === 0) continue
    out.push({ name: o.name, options })
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
