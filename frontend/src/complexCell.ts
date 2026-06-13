// Parsing helpers for the built-in "default views" of ClickHouse complex types
// (Array / Map / Tuple). No React here — the rendering component lives in
// ComplexCell.tsx. See docs/query.md ("Default views for complex types").
//
// Two concerns, both built on one nesting-aware splitter:
//   1. parseComplexType: a ClickHouse *type* string → a small descriptor.
//   2. parseArray/parseTuple/parseMap/unquoteScalar: a serialized *value*
//      (as it appears in TabSeparatedWithNames) → structured substrings.

export type ComplexType =
  | { kind: 'array'; element: ComplexType | null } // element set only for Tuple/Map elements
  | { kind: 'map' }
  | { kind: 'tuple'; fields: { name: string | null }[] }

// Split `s` on top-level occurrences of `sep`, respecting [] () {} nesting and
// single-quoted strings (with backslash escapes). `sep` is a single character.
export function splitTopLevel(s: string, sep: string): string[] {
  const out: string[] = []
  let depth = 0
  let inStr = false
  let start = 0
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (inStr) {
      if (c === '\\') i++ // skip the escaped char
      else if (c === "'") inStr = false
      continue
    }
    if (c === "'") inStr = true
    else if (c === '[' || c === '(' || c === '{') depth++
    else if (c === ']' || c === ')' || c === '}') depth--
    else if (c === sep && depth === 0) {
      out.push(s.slice(start, i))
      start = i + 1
    }
  }
  out.push(s.slice(start))
  return out
}

// Match a `Name(<inner>)` wrapper, returning the constructor name and inner text.
function matchWrapper(type: string): { name: string; inner: string } | null {
  const m = /^([A-Za-z0-9_]+)\((.*)\)$/.exec(type.trim())
  if (!m) return null
  return { name: m[1], inner: m[2] }
}

// Parse a ClickHouse type string into a descriptor, or null for scalar/unsupported.
// Nesting is honored one level deep only for Array(Tuple(...)) / Array(Map(...));
// anything deeper is treated as scalar so its elements render as raw substrings.
export function parseComplexType(type: string): ComplexType | null {
  // DESCRIBE pretty-prints complex types across indented lines; the backend
  // forwards the TabSeparated field verbatim, so those newlines arrive as
  // literal `\n` (or, if unescaped, real newlines). Flatten both to single
  // spaces before parsing — type strings never contain string literals, so
  // collapsing whitespace is safe.
  const flat = type.replace(/\\[nt]/g, ' ').replace(/\s+/g, ' ')
  const w = matchWrapper(flat)
  if (!w) return null

  // Transparent wrappers don't change how a value renders.
  if (w.name === 'Nullable' || w.name === 'LowCardinality') {
    return parseComplexType(w.inner)
  }

  if (w.name === 'Array') {
    const inner = matchWrapper(w.inner)
    if (inner && inner.name === 'Tuple') {
      return { kind: 'array', element: parseTupleType(inner.inner) }
    }
    if (inner && inner.name === 'Map') {
      return { kind: 'array', element: { kind: 'map' } }
    }
    return { kind: 'array', element: null }
  }

  if (w.name === 'Map') return { kind: 'map' }

  if (w.name === 'Tuple') return parseTupleType(w.inner)

  return null
}

// Parse the inside of a Tuple(...) into its field descriptor. A field is named
// when it leads with `identifier <type>` (the type token starting uppercase or a
// backtick); otherwise it's positional. A wrong guess falls back to positional.
function parseTupleType(inner: string): ComplexType {
  const fields = splitTopLevel(inner, ',').map((part) => {
    const m = /^([A-Za-z_]\w*)\s+([A-Z`].*)$/.exec(part.trim())
    return { name: m ? m[1] : null }
  })
  return { kind: 'tuple', fields }
}

// Strip the outer bracket pair and split the contents at the top level. Returns
// [] for an empty collection. `open`/`close` are the surrounding delimiters.
function splitBracketed(s: string, open: string, close: string): string[] {
  const t = s.trim()
  if (!t.startsWith(open) || !t.endsWith(close)) return []
  const body = t.slice(1, -1)
  if (body.trim() === '') return []
  return splitTopLevel(body, ',')
}

// Element substrings of a serialized array: "['a','b']" → ["'a'", "'b'"].
export function parseArray(s: string): string[] {
  return splitBracketed(s, '[', ']')
}

// Field substrings of a serialized tuple: "(1,'a')" → ["1", "'a'"].
export function parseTuple(s: string): string[] {
  return splitBracketed(s, '(', ')')
}

// Key/value substring pairs of a serialized map: "{'x':1}" → [["'x'", "1"]].
export function parseMap(s: string): [string, string][] {
  return splitBracketed(s, '{', '}').map((entry) => {
    const [k, ...rest] = splitTopLevel(entry, ':')
    return [k.trim(), rest.join(':').trim()]
  })
}

// Collapsed cells show at most this many items before the expander.
export const PREVIEW_COUNT = 3

// One rendered item: a single line (scalar array element, map entry, tuple
// field) or a group of lines (an Array(Tuple)/Array(Map) element). The collapse
// threshold counts items, so a nested tuple element stays one collapsible unit.
export type CellItem = { lines: string[] }

// The `name: value` / `index: value` lines of a serialized tuple value.
function tupleLines(type: { fields: { name: string | null }[] }, raw: string): string[] {
  return parseTuple(raw).map((field, i) => {
    const label = type.fields[i]?.name ?? String(i)
    return `${label}: ${unquoteScalar(field)}`
  })
}

// The `key → value` lines of a serialized map value.
function mapLines(raw: string): string[] {
  return parseMap(raw).map(([k, v]) => `${unquoteScalar(k)} → ${unquoteScalar(v)}`)
}

// Turn a complex value into its full (expanded) list of items per the type
// descriptor. The component applies PREVIEW_COUNT and the expander on top.
export function complexCellItems(type: ComplexType, raw: string): CellItem[] {
  if (type.kind === 'array') {
    return parseArray(raw).map((el) => {
      // A list of tuples/maps reads best with each element on one line.
      if (type.element?.kind === 'tuple') {
        return { lines: [tupleLines(type.element, el).join(', ')] }
      }
      if (type.element?.kind === 'map') return { lines: [mapLines(el).join(', ')] }
      return { lines: [unquoteScalar(el)] }
    })
  }
  if (type.kind === 'map') {
    return mapLines(raw).map((line) => ({ lines: [line] }))
  }
  // tuple
  return tupleLines(type, raw).map((line) => ({ lines: [line] }))
}

// Strip the surrounding single quotes of a serialized string scalar and unescape
// it; non-quoted scalars (numbers, etc.) pass through unchanged.
export function unquoteScalar(s: string): string {
  const t = s.trim()
  if (t.length < 2 || t[0] !== "'" || t[t.length - 1] !== "'") return t
  const body = t.slice(1, -1)
  return body.replace(/\\(.)/g, (_, c) => {
    if (c === 'n') return '\n'
    if (c === 't') return '\t'
    if (c === 'r') return '\r'
    if (c === '0') return '\0'
    return c // \\ → \, \' → ', etc.
  })
}
