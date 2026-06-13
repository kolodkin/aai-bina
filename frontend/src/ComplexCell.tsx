import { useState } from 'react'
import { complexCellItems, PREVIEW_COUNT, type ComplexType } from './complexCell'

// Built-in default view for an Array/Map/Tuple result cell: a plain vertical
// list of items, collapsed to the first PREVIEW_COUNT with an expander. See
// docs/query.md ("Default views for complex types"). Values are plain text
// nodes (React-escaped), so DB content can't inject markup.
export function ComplexCell({
  type,
  raw,
  col,
}: {
  type: ComplexType
  raw: string
  col: string
}) {
  const [expanded, setExpanded] = useState(false)
  const items = complexCellItems(type, raw)
  // Empty/unparseable value: fall back to the raw serialized text.
  if (items.length === 0) return <span data-testid={`cell-${col}`}>{raw}</span>

  const collapsible = items.length > PREVIEW_COUNT
  const shown = expanded ? items : items.slice(0, PREVIEW_COUNT)
  const hidden = items.length - PREVIEW_COUNT

  return (
    <div data-testid={`cell-${col}`} className="flex flex-col gap-0.5">
      {shown.map((item, i) => (
        <div
          key={i}
          className={item.lines.length > 1 ? 'border-l border-white/15 pl-2' : undefined}
        >
          {item.lines.map((line, j) => (
            <div key={j}>{line}</div>
          ))}
        </div>
      ))}
      {collapsible && (
        <button
          type="button"
          data-testid={`cell-${col}-toggle`}
          onClick={() => setExpanded((e) => !e)}
          className="self-start text-xs text-slate-400 hover:text-slate-200"
        >
          {expanded ? '▾ collapse' : `… (+${hidden} more) ▸`}
        </button>
      )}
    </div>
  )
}
