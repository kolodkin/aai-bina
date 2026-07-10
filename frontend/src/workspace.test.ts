import { afterEach, describe, expect, it, vi } from 'vitest'
import { activeWorkspace, setActiveWorkspace } from './workspace'

function stubStorage() {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('activeWorkspace', () => {
  it('defaults to "default" with nothing stored', () => {
    stubStorage()
    expect(activeWorkspace()).toBe('default')
  })

  it('round-trips through setActiveWorkspace', () => {
    stubStorage()
    setActiveWorkspace('team-a')
    expect(activeWorkspace()).toBe('team-a')
  })

  it('falls back to "default" when localStorage is unavailable', () => {
    // No stub: node has no localStorage; must not throw.
    expect(activeWorkspace()).toBe('default')
  })
})
