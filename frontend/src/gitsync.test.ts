import { describe, expect, it } from 'vitest'
import { appendRevisions, type GitRevision } from './gitsync'

const rev = (sha: string): GitRevision => ({ sha, date: 0, message: sha })

describe('appendRevisions', () => {
  it('appends new pages in order', () => {
    const merged = appendRevisions([rev('a'), rev('b')], [rev('c'), rev('d')])
    expect(merged.map((r) => r.sha)).toEqual(['a', 'b', 'c', 'd'])
  })

  it('drops duplicates already loaded (overlapping page)', () => {
    const merged = appendRevisions([rev('a'), rev('b')], [rev('b'), rev('c')])
    expect(merged.map((r) => r.sha)).toEqual(['a', 'b', 'c'])
  })

  it('handles an empty first page', () => {
    expect(appendRevisions([], [rev('a')]).map((r) => r.sha)).toEqual(['a'])
  })
})
