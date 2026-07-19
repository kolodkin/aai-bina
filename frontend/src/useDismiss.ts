import { useEffect, useRef, type RefObject } from 'react'

// Dismiss an open popover on a pointerdown outside `ref` or an Escape keypress.
// Put the ref on the container that wraps BOTH the trigger and the popover, so
// clicking the trigger toggles normally instead of closing-then-reopening.
// `active` gates the listeners so they're only attached while open; `onDismiss`
// is read through a ref so an inline arrow doesn't re-subscribe every render.
export function useDismiss(
  active: boolean,
  ref: RefObject<HTMLElement | null>,
  onDismiss: () => void,
): void {
  const cb = useRef(onDismiss)
  useEffect(() => {
    cb.current = onDismiss
  }, [onDismiss])
  useEffect(() => {
    if (!active) return
    // pointerdown (not click) fires before focus shifts, so dismissal feels
    // instant and beats any mousedown-driven refocus.
    const onPointer = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) cb.current()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cb.current()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [active, ref])
}
