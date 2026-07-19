import { describe, expect, it } from 'vitest'

import { branchUrl, commitUrl } from './repoLinks'

describe('commitUrl', () => {
  it('builds host-specific commit paths', () => {
    expect(commitUrl('https://github.com/org/repo', 'abc123')).toBe(
      'https://github.com/org/repo/commit/abc123',
    )
    expect(commitUrl('https://gitlab.com/org/repo', 'abc123')).toBe(
      'https://gitlab.com/org/repo/-/commit/abc123',
    )
    expect(commitUrl('https://bitbucket.org/org/repo', 'abc123')).toBe(
      'https://bitbucket.org/org/repo/commits/abc123',
    )
  })

  it('tolerates a trailing slash on the base', () => {
    expect(commitUrl('https://github.com/org/repo/', 'abc')).toBe(
      'https://github.com/org/repo/commit/abc',
    )
  })

  it('returns null for unsupported hosts, null, or garbage', () => {
    expect(commitUrl('https://git.acme.internal/org/repo', 'abc')).toBeNull()
    expect(commitUrl('https://enterprise.github.example.com/o/r', 'abc')).toBeNull()
    expect(commitUrl(null, 'abc')).toBeNull()
    expect(commitUrl('not a url', 'abc')).toBeNull()
  })
})

describe('branchUrl', () => {
  it('builds host-specific tree paths', () => {
    expect(branchUrl('https://github.com/org/repo', 'main')).toBe(
      'https://github.com/org/repo/tree/main',
    )
    expect(branchUrl('https://gitlab.com/org/repo', 'main')).toBe(
      'https://gitlab.com/org/repo/-/tree/main',
    )
    expect(branchUrl('https://bitbucket.org/org/repo', 'main')).toBe(
      'https://bitbucket.org/org/repo/src/main',
    )
  })

  it('encodes segments but keeps slashes in nested branch names', () => {
    expect(branchUrl('https://github.com/org/repo', 'feature/a b')).toBe(
      'https://github.com/org/repo/tree/feature/a%20b',
    )
  })

  it('returns null for unsupported hosts or null', () => {
    expect(branchUrl('https://git.acme.internal/o/r', 'main')).toBeNull()
    expect(branchUrl(null, 'main')).toBeNull()
  })
})
