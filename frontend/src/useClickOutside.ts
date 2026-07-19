import { useEffect, useRef, type RefObject } from 'react'

// Dismiss an open popover when a pointerdown lands outside `ref`. Put the ref on
// the container that wraps BOTH the trigger and the popover, so clicking the
// trigger toggles normally instead of closing-then-reopening. `active` gates the
// listener so it's only attached while the popover is open; `onOutside` is read
// through a ref so an inline arrow doesn't re-subscribe every render.
export function useClickOutside(
  active: boolean,
  ref: RefObject<HTMLElement | null>,
  onOutside: () => void,
): void {
  const cb = useRef(onOutside)
  useEffect(() => {
    cb.current = onOutside
  }, [onOutside])
  useEffect(() => {
    if (!active) return
    const handler = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) cb.current()
    }
    // pointerdown (not click) fires before focus shifts, so dismissal feels
    // instant and beats any mousedown-driven refocus.
    document.addEventListener('pointerdown', handler)
    return () => document.removeEventListener('pointerdown', handler)
  }, [active, ref])
}
