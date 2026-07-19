import { describe, expect, test, vi } from 'vitest'
import {
  answerBridgeRequest,
  buildSrcDoc,
  parseBridgeRequest,
  parseParamsRequest,
} from './dashboardBridge'

describe('parseParamsRequest', () => {
  test('accepts a set-params message', () => {
    expect(parseParamsRequest({ type: 'set-params', values: { field: 'city' } })).toEqual({
      field: 'city',
    })
  })

  test('coerces checkbox booleans to the on/off strings substitution expects', () => {
    expect(parseParamsRequest({ type: 'set-params', values: { category: true, region: false } })).toEqual({
      category: 'on',
      region: '',
    })
  })

  test('rejects a message of another type', () => {
    expect(parseParamsRequest({ type: 'run-queries', values: {} })).toBeNull()
  })

  test('rejects values that are not a mapping', () => {
    expect(parseParamsRequest({ type: 'set-params', values: ['a'] })).toBeNull()
  })
})

describe('buildSrcDoc', () => {
  test('exposes the results as window.queries', () => {
    expect(buildSrcDoc('<p>hi</p>', { a: { n: [1] } })).toContain('window.queries = {"a":{"n":[1]}}')
  })

  test('escapes < so result data cannot close the prologue script', () => {
    const doc = buildSrcDoc('<p>hi</p>', { a: { n: ['</script><img>'] } })
    expect(doc).not.toContain('</script><img>')
    expect(doc).toContain('\\u003c/script>')
  })

  test('exposes window.runQueries for querying after load', () => {
    expect(buildSrcDoc('<p>hi</p>', {})).toContain('window.runQueries')
  })

  test('exposes the resolved params so the page can render its own controls', () => {
    const doc = buildSrcDoc('<p>hi</p>', {}, [
      { name: 'field', kind: 'identifier', options: ['city', 'severity'], value: 'city' },
    ])
    expect(doc).toContain('window.params = [{"name":"field"')
    expect(doc).toContain('"value":"city"')
  })

  test('exposes window.setParams for requesting a re-run', () => {
    expect(buildSrcDoc('<p>hi</p>', {})).toContain('window.setParams')
  })

  test('publishes an empty params list when the dashboard declares none', () => {
    expect(buildSrcDoc('<p>hi</p>', {})).toContain('window.params = []')
  })

  test('keeps the dashboard html after the prologue', () => {
    expect(buildSrcDoc('<p>hi</p>', {})).toMatch(/<\/script>\n<p>hi<\/p>$/)
  })
})

describe('parseBridgeRequest', () => {
  test('accepts a run-queries message', () => {
    expect(
      parseBridgeRequest({ type: 'run-queries', id: 'r1', queries: { a: 'SELECT 1' } }),
    ).toEqual({ id: 'r1', queries: { a: 'SELECT 1' } })
  })

  test('rejects a message of another type', () => {
    expect(parseBridgeRequest({ type: 'something-else', id: 'r1', queries: {} })).toBeNull()
  })

  test('rejects a message without a string id', () => {
    expect(parseBridgeRequest({ type: 'run-queries', queries: { a: 'SELECT 1' } })).toBeNull()
  })

  test('rejects queries whose SQL is not a string', () => {
    expect(parseBridgeRequest({ type: 'run-queries', id: 'r1', queries: { a: 7 } })).toBeNull()
  })

  test('rejects non-object payloads', () => {
    expect(parseBridgeRequest('run-queries')).toBeNull()
  })
})

describe('answerBridgeRequest', () => {
  test('answers with the results the runner returns', async () => {
    const run = vi.fn(async () => ({ ok: true as const, results: { a: { n: [1] } } }))

    const answer = await answerBridgeRequest({ id: 'r1', queries: { a: 'SELECT 1 AS n' } }, run)

    expect(run).toHaveBeenCalledWith({ a: 'SELECT 1 AS n' })
    expect(answer).toEqual({ type: 'query-results', id: 'r1', ok: true, results: { a: { n: [1] } } })
  })

  test('answers with the failure message when the runner fails', async () => {
    const run = async () => ({ ok: false as const, message: 'connection timed out' })

    const answer = await answerBridgeRequest({ id: 'r2', queries: { a: 'SELECT 1' } }, run)

    expect(answer).toEqual({ type: 'query-results', id: 'r2', ok: false, message: 'connection timed out' })
  })

  test('answers with a failure when the runner throws', async () => {
    const run = async () => {
      throw new Error('network down')
    }

    const answer = await answerBridgeRequest({ id: 'r3', queries: { a: 'SELECT 1' } }, run)

    expect(answer).toEqual({ type: 'query-results', id: 'r3', ok: false, message: 'Failed to run queries.' })
  })
})
