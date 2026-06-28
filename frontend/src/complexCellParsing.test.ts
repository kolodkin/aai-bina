import { describe, expect, test } from 'vitest'
import {
  parseComplexType,
  splitTopLevel,
  parseArray,
  parseTuple,
  parseMap,
  unquoteScalar,
  complexCellItems,
  PREVIEW_COUNT,
} from './complexCellParsing'

describe('parseComplexType', () => {
  test('Array(String) → scalar array', () => {
    expect(parseComplexType('Array(String)')).toEqual({
      kind: 'array',
      element: null,
    })
  })

  test('Map(String, UInt64) → map', () => {
    expect(parseComplexType('Map(String, UInt64)')).toEqual({ kind: 'map' })
  })

  test('named Tuple captures field names', () => {
    expect(parseComplexType('Tuple(id Int32, name String)')).toEqual({
      kind: 'tuple',
      fields: [{ name: 'id' }, { name: 'name' }],
    })
  })

  test('unnamed Tuple has positional (null-name) fields', () => {
    expect(parseComplexType('Tuple(Int32, String)')).toEqual({
      kind: 'tuple',
      fields: [{ name: null }, { name: null }],
    })
  })

  test('Array(Tuple(...)) captures the inner tuple descriptor', () => {
    expect(parseComplexType('Array(Tuple(id Int32, name String))')).toEqual({
      kind: 'array',
      element: { kind: 'tuple', fields: [{ name: 'id' }, { name: 'name' }] },
    })
  })

  test('Array(Map(...)) captures the inner map descriptor', () => {
    expect(parseComplexType('Array(Map(String, UInt64))')).toEqual({
      kind: 'array',
      element: { kind: 'map' },
    })
  })

  test('Array(Nullable(String)) is still a scalar array', () => {
    expect(parseComplexType('Array(Nullable(String))')).toEqual({
      kind: 'array',
      element: null,
    })
  })

  test('tolerates DESCRIBE pretty-printed types (literal \\n escapes + indent)', () => {
    // ClickHouse DESCRIBE pretty-prints complex types across lines; the backend
    // forwards the TabSeparated field verbatim, so newlines arrive as literal
    // backslash-n sequences with indentation.
    expect(parseComplexType('Tuple(\\n    id Int32,\\n    name String)')).toEqual({
      kind: 'tuple',
      fields: [{ name: 'id' }, { name: 'name' }],
    })
    expect(
      parseComplexType('Array(Tuple(\\n    id Int32,\\n    name String))'),
    ).toEqual({
      kind: 'array',
      element: { kind: 'tuple', fields: [{ name: 'id' }, { name: 'name' }] },
    })
  })

  test('tolerates real newlines/indentation in types too', () => {
    expect(parseComplexType('Tuple(\n    id Int32,\n    name String)')).toEqual({
      kind: 'tuple',
      fields: [{ name: 'id' }, { name: 'name' }],
    })
  })

  test('a non-complex type → null', () => {
    expect(parseComplexType('String')).toBeNull()
    expect(parseComplexType('UInt64')).toBeNull()
    expect(parseComplexType('')).toBeNull()
  })

  test('outer Nullable/LowCardinality wrappers are unwrapped', () => {
    expect(parseComplexType('LowCardinality(Array(String))')).toEqual({
      kind: 'array',
      element: null,
    })
  })
})

describe('splitTopLevel', () => {
  test('splits on top-level commas only', () => {
    expect(splitTopLevel('1,2,3', ',')).toEqual(['1', '2', '3'])
  })

  test('respects nested brackets/parens/braces', () => {
    expect(splitTopLevel("(1,'a'),(2,'b')", ',')).toEqual(["(1,'a')", "(2,'b')"])
    expect(splitTopLevel('[1,2],[3,4]', ',')).toEqual(['[1,2]', '[3,4]'])
  })

  test('ignores separators inside single-quoted strings', () => {
    expect(splitTopLevel("'a,b','c'", ',')).toEqual(["'a,b'", "'c'"])
  })

  test('ignores escaped quotes inside strings', () => {
    expect(splitTopLevel("'a\\'b','c'", ',')).toEqual(["'a\\'b'", "'c'"])
  })
})

describe('parseArray', () => {
  test('splits a scalar array into element substrings', () => {
    expect(parseArray("['a','b','c']")).toEqual(["'a'", "'b'", "'c'"])
  })

  test('empty array → []', () => {
    expect(parseArray('[]')).toEqual([])
  })

  test('array of tuples keeps each tuple substring intact', () => {
    expect(parseArray("[(1,'a'),(2,'b')]")).toEqual(["(1,'a')", "(2,'b')"])
  })
})

describe('parseTuple', () => {
  test('splits a tuple into field substrings', () => {
    expect(parseTuple("(1,'a')")).toEqual(['1', "'a'"])
  })
})

describe('parseMap', () => {
  test('splits a map into key/value substring pairs', () => {
    expect(parseMap("{'x':1,'y':2}")).toEqual([
      ["'x'", '1'],
      ["'y'", '2'],
    ])
  })

  test('empty map → []', () => {
    expect(parseMap('{}')).toEqual([])
  })
})

describe('unquoteScalar', () => {
  test('strips surrounding single quotes', () => {
    expect(unquoteScalar("'hello'")).toBe('hello')
  })

  test('unescapes backslash escapes', () => {
    expect(unquoteScalar("'a\\'b\\\\c'")).toBe("a'b\\c")
    expect(unquoteScalar("'line1\\nline2'")).toBe('line1\nline2')
  })

  test('non-quoted scalars pass through unchanged', () => {
    expect(unquoteScalar('42')).toBe('42')
  })
})

const arrayT = parseComplexType('Array(String)')!
const mapT = parseComplexType('Map(String, UInt64)')!
const namedTupleT = parseComplexType('Tuple(id Int32, name String)')!
const unnamedTupleT = parseComplexType('Tuple(Int32, String)')!
const arrayTupleT = parseComplexType('Array(Tuple(id Int32, name String))')!
const arrayMapT = parseComplexType('Array(Map(String, UInt64))')!

describe('complexCellItems', () => {
  test('scalar array → one unquoted element per item', () => {
    expect(complexCellItems(arrayT, "['a','b']")).toEqual([
      { lines: ['a'] },
      { lines: ['b'] },
    ])
  })

  test('map → one "key → value" line per entry', () => {
    expect(complexCellItems(mapT, "{'x':1,'y':2}")).toEqual([
      { lines: ['x → 1'] },
      { lines: ['y → 2'] },
    ])
  })

  test('named tuple → one "name: value" line per field', () => {
    expect(complexCellItems(namedTupleT, "(1,'a')")).toEqual([
      { lines: ['id: 1'] },
      { lines: ['name: a'] },
    ])
  })

  test('unnamed tuple uses positional indices', () => {
    expect(complexCellItems(unnamedTupleT, "(1,'a')")).toEqual([
      { lines: ['0: 1'] },
      { lines: ['1: a'] },
    ])
  })

  test('Array(Tuple) → one item per element, each tuple on a single line', () => {
    expect(complexCellItems(arrayTupleT, "[(1,'a'),(2,'b')]")).toEqual([
      { lines: ['id: 1, name: a'] },
      { lines: ['id: 2, name: b'] },
    ])
  })

  test('Array(Map) → one item per element, each map on a single line', () => {
    expect(complexCellItems(arrayMapT, "[{'x':1},{'y':2,'z':3}]")).toEqual([
      { lines: ['x → 1'] },
      { lines: ['y → 2, z → 3'] },
    ])
  })

  test('empty collection → no items', () => {
    expect(complexCellItems(arrayT, '[]')).toEqual([])
  })

  test('PREVIEW_COUNT is 3', () => {
    expect(PREVIEW_COUNT).toBe(3)
  })
})
