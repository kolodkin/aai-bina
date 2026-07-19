// Explorer Tables-sidebar sizing (localStorage). Four preset states walked by
// the edge-rail's cycle button, plus free drag within the split states.
// See docs/explorer.md.

// Preset indices for the cycle: hidden → minimal → expanded → full → hidden.
export const HIDDEN = 0
export const MINIMAL = 1
export const EXPANDED = 2
export const FULL = 3

export type SidebarState = { presetIndex: number; width: number }

// Drag bounds and the canonical widths the cycle button snaps to.
const MIN_WIDTH = 180
const MAX_WIDTH = 560
const MINIMAL_WIDTH = 256
const EXPANDED_WIDTH = 480

const DEFAULT: SidebarState = { presetIndex: MINIMAL, width: MINIMAL_WIDTH }

const KEY = 'qv_explorer_sidebar'

// Clamp a dragged width to the resizable range.
export function clampWidth(px: number): number {
  if (!Number.isFinite(px)) return MINIMAL_WIDTH
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(px)))
}

// The cycle button's state machine. Advancing lands on the next preset;
// minimal/expanded snap `width` to their canonical value, hidden/full carry the
// current split width forward so returning to a split state restores it.
export function nextPreset(state: SidebarState): SidebarState {
  const next = (state.presetIndex + 1) % 4
  if (next === MINIMAL) return { presetIndex: MINIMAL, width: MINIMAL_WIDTH }
  if (next === EXPANDED) return { presetIndex: EXPANDED, width: EXPANDED_WIDTH }
  return { presetIndex: next, width: state.width }
}

// True in the split states (panel + rows side by side), where drag applies.
export function isSplit(presetIndex: number): boolean {
  return presetIndex === MINIMAL || presetIndex === EXPANDED
}

// Read + validate the stored state; fall back to the default on anything
// missing, malformed, or out of range.
export function loadSidebarState(): SidebarState {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULT }
    const parsed = JSON.parse(raw) as Partial<SidebarState>
    const presetIndex = parsed.presetIndex
    if (
      typeof presetIndex !== 'number' ||
      presetIndex < HIDDEN ||
      presetIndex > FULL
    ) {
      return { ...DEFAULT }
    }
    return { presetIndex: Math.trunc(presetIndex), width: clampWidth(parsed.width ?? MINIMAL_WIDTH) }
  } catch {
    return { ...DEFAULT }
  }
}

export function saveSidebarState(state: SidebarState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state))
  } catch {
    /* non-persistent contexts still work within the page's lifetime */
  }
}
