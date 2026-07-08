import { describe, expect, test } from 'vitest'
import { suggestCompletions } from './promptSuggestions'

const drivers = ['clickhouse', 'postgres', 'duckdb']
const ctx = (over = {}) => ({ drivers, ready: false, connected: false, ...over })

const labels = (input: string, over = {}) =>
  suggestCompletions(input, ctx(over)).map((s) => s.label)
const values = (input: string, over = {}) =>
  suggestCompletions(input, ctx(over)).map((s) => s.value)

describe('command completion', () => {
  test('"n" suggests new', () => {
    expect(labels('n')).toEqual(['new'])
    // Accepting leaves a trailing space so the driver dropdown follows.
    expect(values('n')).toEqual(['new '])
  })

  test('empty input lists always-available commands', () => {
    expect(labels('')).toEqual(['new', 'connect', 'dashboard'])
  })

  test('leading whitespace is ignored', () => {
    expect(labels('  ne')).toEqual(['new'])
  })

  test('query only appears when ready', () => {
    expect(labels('q')).toEqual([])
    expect(labels('q', { ready: true })).toEqual(['query'])
  })

  test('disconnect only appears when connected', () => {
    expect(labels('d')).toEqual(['dashboard'])
    expect(labels('d', { connected: true })).toEqual(['dashboard', 'disconnect'])
  })

  test('connect (an arg command) accepts with a trailing space', () => {
    expect(values('conn')).toEqual(['connect '])
  })

  test('query (a no-arg command) accepts without a trailing space', () => {
    expect(values('que', { ready: true })).toEqual(['query'])
  })
})

describe('driver completion after "new "', () => {
  test('"new " lists all drivers', () => {
    expect(labels('new ')).toEqual(drivers)
    expect(values('new ')).toEqual(['new clickhouse', 'new postgres', 'new duckdb'])
  })

  test('"new p" narrows to postgres', () => {
    expect(labels('new p')).toEqual(['postgres'])
  })

  test('unknown driver prefix yields nothing', () => {
    expect(labels('new x')).toEqual([])
  })

  test('command word is matched case-insensitively', () => {
    expect(labels('NEW ')).toEqual(drivers)
  })

  test('dashboard has no value completions', () => {
    expect(suggestCompletions('dashboard bar', ctx())).toEqual([])
  })
})

describe('connection completion after "connect "', () => {
  const connections = ['prod-ch', 'staging-pg', 'local']
  const cctx = (over = {}) => ctx({ connections, ...over })

  test('"connect " lists all saved connections, recency order preserved', () => {
    expect(suggestCompletions('connect ', cctx()).map((s) => s.label)).toEqual(connections)
    expect(suggestCompletions('connect ', cctx()).map((s) => s.value)).toEqual([
      'connect prod-ch',
      'connect staging-pg',
      'connect local',
    ])
  })

  test('"connect s" narrows by prefix', () => {
    expect(suggestCompletions('connect s', cctx()).map((s) => s.label)).toEqual(['staging-pg'])
  })

  test('no saved connections yields nothing', () => {
    expect(suggestCompletions('connect ', ctx())).toEqual([])
  })

  test('matching is case-insensitive', () => {
    expect(suggestCompletions('connect PROD', cctx()).map((s) => s.label)).toEqual(['prod-ch'])
  })
})
