import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  EXPANDED,
  FULL,
  HIDDEN,
  MINIMAL,
  clampWidth,
  isSplit,
  loadSidebarState,
  nextPreset,
  saveSidebarState,
} from './explorerSidebar'

function stubStorage() {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('clampWidth', () => {
  it('clamps below the minimum up to 180', () => {
    expect(clampWidth(50)).toBe(180)
  })
  it('clamps above the maximum down to 560', () => {
    expect(clampWidth(9000)).toBe(560)
  })
  it('rounds and passes in-range values through', () => {
    expect(clampWidth(321.6)).toBe(322)
  })
  it('falls back to 256 on a non-finite value', () => {
    expect(clampWidth(NaN)).toBe(256)
  })
})

describe('nextPreset', () => {
  it('walks hidden → minimal → expanded → full → hidden', () => {
    let s = { presetIndex: HIDDEN, width: 300 }
    s = nextPreset(s)
    expect(s).toEqual({ presetIndex: MINIMAL, width: 256 })
    s = nextPreset(s)
    expect(s).toEqual({ presetIndex: EXPANDED, width: 480 })
    s = nextPreset(s)
    expect(s.presetIndex).toBe(FULL)
    s = nextPreset(s)
    expect(s.presetIndex).toBe(HIDDEN)
  })

  it('carries the split width through hidden and full', () => {
    // Drag to a custom width in expanded, then cycle to full and hidden.
    const dragged = { presetIndex: EXPANDED, width: 375 }
    const full = nextPreset(dragged)
    expect(full).toEqual({ presetIndex: FULL, width: 375 })
    const hidden = nextPreset(full)
    expect(hidden).toEqual({ presetIndex: HIDDEN, width: 375 })
    // Returning to minimal snaps to its canonical width.
    expect(nextPreset(hidden)).toEqual({ presetIndex: MINIMAL, width: 256 })
  })
})

describe('isSplit', () => {
  it('is true only in minimal and expanded', () => {
    expect(isSplit(HIDDEN)).toBe(false)
    expect(isSplit(MINIMAL)).toBe(true)
    expect(isSplit(EXPANDED)).toBe(true)
    expect(isSplit(FULL)).toBe(false)
  })
})

describe('loadSidebarState / saveSidebarState', () => {
  it('defaults to minimal/256 with nothing stored', () => {
    stubStorage()
    expect(loadSidebarState()).toEqual({ presetIndex: MINIMAL, width: 256 })
  })

  it('round-trips a saved state', () => {
    stubStorage()
    saveSidebarState({ presetIndex: EXPANDED, width: 375 })
    expect(loadSidebarState()).toEqual({ presetIndex: EXPANDED, width: 375 })
  })

  it('falls back to the default on an out-of-range preset', () => {
    stubStorage()
    localStorage.setItem('qv_explorer_sidebar', JSON.stringify({ presetIndex: 9, width: 300 }))
    expect(loadSidebarState()).toEqual({ presetIndex: MINIMAL, width: 256 })
  })

  it('clamps an out-of-range stored width', () => {
    stubStorage()
    localStorage.setItem('qv_explorer_sidebar', JSON.stringify({ presetIndex: MINIMAL, width: 9000 }))
    expect(loadSidebarState()).toEqual({ presetIndex: MINIMAL, width: 560 })
  })

  it('falls back to the default when localStorage is unavailable', () => {
    // No stub: node has no localStorage; must not throw.
    expect(loadSidebarState()).toEqual({ presetIndex: MINIMAL, width: 256 })
  })
})
