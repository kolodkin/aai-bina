import { describe, expect, test } from 'vitest'
import {
  parseDashboardParams,
  placeholdersIn,
  resolveOrder,
  substituteParams,
} from './dashboardParams'

describe('parseDashboardParams', () => {
  test('parses a static options param', () => {
    expect(parseDashboardParams([{ name: 'region', options: ['eu', 'us'] }])).toEqual([
      { name: 'region', kind: 'value', options: ['eu', 'us'] },
    ])
  })

  test('parses an options_sql param', () => {
    expect(
      parseDashboardParams([{ name: 'table', kind: 'identifier', options_sql: 'SELECT name FROM system.tables' }]),
    ).toEqual([{ name: 'table', kind: 'identifier', optionsSql: 'SELECT name FROM system.tables' }])
  })

  test('parses a dimension param, which needs no options', () => {
    expect(parseDashboardParams([{ name: 'category', kind: 'dimension' }])).toEqual([
      { name: 'category', kind: 'dimension' },
    ])
  })

  test('drops an entry declaring both options and options_sql', () => {
    expect(parseDashboardParams([{ name: 'x', options: ['a'], options_sql: 'SELECT 1' }])).toEqual([])
  })

  test('drops an entry with an unknown kind', () => {
    expect(parseDashboardParams([{ name: 'x', kind: 'sql', options: ['a'] }])).toEqual([])
  })

  test('drops a nameless entry', () => {
    expect(parseDashboardParams([{ options: ['a'] }])).toEqual([])
  })

  test('returns nothing for a non-array', () => {
    expect(parseDashboardParams({ name: 'x' })).toEqual([])
  })
})

describe('substituteParams', () => {
  const params = parseDashboardParams([
    { name: 'table', kind: 'identifier', options_sql: 'SELECT 1' },
    { name: 'field', kind: 'identifier', options_sql: 'SELECT 1' },
    { name: 'region', options: ['eu'] },
    { name: 'category', kind: 'dimension' },
  ])

  test('substitutes an identifier as a quoted identifier, not a string literal', () => {
    expect(substituteParams('SELECT {field} FROM {table}', params, { field: 'city', table: 'events' }))
      .toBe('SELECT `city` FROM `events`')
  })

  test('substitutes a value as a quoted literal', () => {
    expect(substituteParams('WHERE region = {region}', params, { region: 'eu' })).toBe("WHERE region = 'eu'")
  })

  test('escapes quotes in a value literal', () => {
    expect(substituteParams('WHERE region = {region}', params, { region: "o'brien" })).toBe(
      "WHERE region = 'o''brien'",
    )
  })

  test('rejects an identifier containing a backtick', () => {
    expect(() => substituteParams('SELECT {field}', params, { field: 'a`b' })).toThrow(/identifier/i)
  })

  test('substitutes a checked dimension as its column', () => {
    expect(substituteParams('GROUP BY {category}', params, { category: 'on' })).toBe('GROUP BY `category`')
  })

  test("substitutes an unchecked dimension as '' so the query shape is unchanged", () => {
    expect(substituteParams('GROUP BY {category}', params, { category: '' })).toBe("GROUP BY ''")
  })

  test('casts an identifier to a literal where SQL wants a string', () => {
    // system.columns.table is compared against a string, but the same param is
    // an identifier in `FROM {table}`.
    expect(substituteParams('WHERE table = {table:literal} FROM {table}', params, { table: 'events' })).toBe(
      "WHERE table = 'events' FROM `events`",
    )
  })

  test('casts a value to an identifier', () => {
    expect(substituteParams('SELECT {region:identifier}', params, { region: 'eu' })).toBe('SELECT `eu`')
  })

  test('rejects an unknown cast', () => {
    expect(() => substituteParams('SELECT {field:sql}', params, { field: 'city' })).toThrow(/cast/i)
  })

  test('leaves an unknown placeholder untouched', () => {
    expect(substituteParams('SELECT {nope}', params, {})).toBe('SELECT {nope}')
  })

  test('throws when a declared param has no value', () => {
    expect(() => substituteParams('SELECT {field}', params, {})).toThrow(/field/)
  })
})

describe('placeholdersIn', () => {
  test('finds the placeholders a query depends on', () => {
    expect(placeholdersIn('SELECT name FROM system.columns WHERE table = {table} AND x = {y}')).toEqual(['table', 'y'])
  })

  test('finds a cast placeholder by its param name', () => {
    expect(placeholdersIn('WHERE table = {table:literal}')).toEqual(['table'])
  })

  test('finds none in a plain query', () => {
    expect(placeholdersIn('SELECT 1')).toEqual([])
  })
})

describe('resolveOrder', () => {
  test('orders a dependent param after the one its options_sql references', () => {
    const params = parseDashboardParams([
      { name: 'field', kind: 'identifier', options_sql: 'SELECT name FROM system.columns WHERE table = {table}' },
      { name: 'table', kind: 'identifier', options_sql: 'SELECT name FROM system.tables' },
    ])
    expect(resolveOrder(params).map((p) => p.name)).toEqual(['table', 'field'])
  })

  test('keeps declaration order when nothing depends on anything', () => {
    const params = parseDashboardParams([
      { name: 'a', options: ['1'] },
      { name: 'b', options: ['2'] },
    ])
    expect(resolveOrder(params).map((p) => p.name)).toEqual(['a', 'b'])
  })

  test('throws on a dependency cycle rather than looping', () => {
    const params = parseDashboardParams([
      { name: 'a', kind: 'identifier', options_sql: 'SELECT {b}' },
      { name: 'b', kind: 'identifier', options_sql: 'SELECT {a}' },
    ])
    expect(() => resolveOrder(params)).toThrow(/cycle/i)
  })
})
