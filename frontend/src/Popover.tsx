import { useRef, type ReactNode, type UIEvent } from 'react'

import { useDismiss } from './useDismiss'

type Props = {
  open: boolean
  onDismiss: () => void
  // The control that toggles the popover. It lives inside the dismiss root, so
  // clicking it toggles normally instead of closing-then-reopening.
  trigger: ReactNode
  children: ReactNode
  // Which edge the panel hangs from; popovers near the right of the screen
  // align right so they don't overflow.
  align?: 'left' | 'right'
  // Panel sizing/scroll classes (width, max-height, overflow); the frame,
  // offset and z-index are the component's own.
  panelClassName?: string
  // 'popover' is the standard chrome; 'panel' is the heavier surface used for
  // list popovers.
  surface?: 'popover' | 'panel'
  testId?: string
  // ARIA role for the panel, when it is a listbox rather than a plain surface.
  panelRole?: string
  onScroll?: (e: UIEvent<HTMLDivElement>) => void
}

// A popover anchored to its trigger, dismissed by an outside pointerdown or
// Escape. Every popover in the app goes through this so none can forget the
// dismiss wiring — the bug this replaced was a hand-rolled one that did.
export default function Popover({
  open,
  onDismiss,
  trigger,
  children,
  align = 'left',
  panelClassName = '',
  surface = 'popover',
  testId,
  panelRole,
  onScroll,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  useDismiss(open, rootRef, onDismiss)

  const surfaceClass = surface === 'panel' ? 'glass-panel' : 'glass-popover'
  const alignClass = align === 'right' ? 'right-0' : 'left-0'

  return (
    <div className="relative" ref={rootRef}>
      {trigger}
      {open && (
        <div
          data-testid={testId}
          role={panelRole}
          onScroll={onScroll}
          className={`${surfaceClass} absolute ${alignClass} top-full z-10 mt-2 ${panelClassName}`}
        >
          {children}
        </div>
      )}
    </div>
  )
}
