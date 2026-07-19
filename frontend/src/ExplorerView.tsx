import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { FieldPickers, type Field, type OrderCol } from './FieldPickers'
import { isReady, type Connection } from './QueryView'
import { ResultsTable } from './ResultsTable'
import { shownColumnIndices } from './presentation'
import { formatBytes, formatCompact } from './compactNumber'
import { parseTsv } from './tsv'
import {
  FULL,
  HIDDEN,
  MINIMAL,
  clampWidth,
  isSplit,
  loadSidebarState,
  nextPreset,
  saveSidebarState,
} from './explorerSidebar'

// Sidebar entry from /api/db/tables. rows/bytes are engine estimates — null
// when the engine doesn't track them (views, never-analyzed Postgres tables);
// query is the ready-to-run browse SELECT, quoted server-side with the
// driver's identifier quote.
type TableInfo = { name: string; rows: number | null; bytes: number | null; query: string }

// The "1.2K rows · 3.4MB" subline, or null when the engine knows neither.
function tableMeta(t: TableInfo): string | null {
  const parts = [
    t.rows != null ? `${formatCompact(t.rows)} rows` : null,
    t.bytes != null ? formatBytes(t.bytes) : null,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

// The classical table-navigator page (`/explorer?table=x`): a sidebar lists the
// active database's tables; picking one browses its rows with the same field
// select / order-by select as the query panel. Field selection is client-side
// column visibility, order-by and pagination go to the server.
function ExplorerView({ connection }: { connection: Connection | null }) {
  const ready = isReady(connection)
  const database = connection?.database ?? null
  const [searchParams, setSearchParams] = useSearchParams()
  const table = searchParams.get('table') ?? ''

  const [tables, setTables] = useState<TableInfo[]>([])
  const [tablesError, setTablesError] = useState<string | null>(null)
  const [fields, setFields] = useState<Field[]>([])
  const [visibleCols, setVisibleCols] = useState<string[]>([])
  const [orderBy, setOrderBy] = useState<OrderCol[]>([])
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [output, setOutput] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // The limit the current output was fetched with, so a no-op blur of the
  // Limit input doesn't refetch the page.
  const appliedLimit = useRef(100)

  // Tables-sidebar sizing: preset state (hidden/minimal/expanded/full) walked by
  // the edge-rail cycle button, plus free drag within the split states. Seeded
  // from and persisted to localStorage. See docs/explorer.md.
  const [sidebar, setSidebar] = useState(loadSidebarState)
  useEffect(() => {
    saveSidebarState(sidebar)
  }, [sidebar])

  // Drag the divider between the panel and rows: pointer capture keeps the drag
  // alive off the handle; each move clamps the sidebar's left edge to the width.
  const rowRef = useRef<HTMLDivElement>(null)
  function startResize(e: React.PointerEvent) {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const left = rowRef.current?.getBoundingClientRect().left ?? 0
    const onMove = (ev: PointerEvent) => {
      setSidebar((s) => ({ ...s, width: clampWidth(ev.clientX - left) }))
    }
    const onUp = (ev: PointerEvent) => {
      ;(e.target as Element).releasePointerCapture?.(ev.pointerId)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  // The sidebar entry the URL selects, once the list has it. Row loading keys
  // off this: nothing fires until the table is confirmed present, so a stale
  // selection (e.g. after a database switch) never issues doomed queries.
  const selected = tables.find((t) => t.name === table)

  // Load the sidebar whenever the active database changes; a selected table
  // that vanished (database switch) is dropped from the URL.
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    void (async () => {
      try {
        const res = await fetch('/api/db/tables')
        const data = await res.json()
        if (cancelled) return
        if (data.ok) {
          const list = (data.tables ?? []) as TableInfo[]
          setTables(list)
          setTablesError(null)
          if (table && !list.some((t) => t.name === table)) {
            setSearchParams({}, { replace: true })
          }
        } else {
          setTables([])
          setTablesError((data.message as string) ?? 'failed to list tables')
        }
      } catch (err) {
        if (!cancelled) {
          setTables([])
          setTablesError(err instanceof Error ? err.message : 'request failed')
        }
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, database])

  const runQuery = useCallback(
    async (sql: string, lim: number, off: number, ord: OrderCol[]) => {
      setBusy(true)
      setError(null)
      try {
        const res = await fetch('/api/db/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: sql,
            limit: lim,
            offset: off,
            format: 'text',
            order_by: ord,
          }),
        })
        const data = await res.json()
        if (data.ok) {
          setOutput(data.output as string)
          setOffset(off)
          appliedLimit.current = lim
        } else {
          setError((data.message as string) ?? 'query failed')
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'request failed')
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  // A confirmed selection (also after a database switch reloads the list):
  // reset the presentation, then describe and load the first page — the two
  // requests are independent, so they run concurrently.
  useEffect(() => {
    if (!ready || !selected) return
    const sql = selected.query
    let cancelled = false
    /* eslint-disable react-hooks/set-state-in-effect */
    setOutput(null)
    setError(null)
    setFields([])
    setVisibleCols([])
    setOrderBy([])
    setOffset(0)
    /* eslint-enable react-hooks/set-state-in-effect */
    void (async () => {
      try {
        const res = await fetch('/api/db/describe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: sql }),
        })
        const data = await res.json()
        if (!cancelled && data.ok) {
          const next = (data.fields ?? []) as Field[]
          setFields(next)
          setVisibleCols(next.map((f) => f.name))
        }
        // A failed describe isn't fatal: the run reports the real error.
      } catch {
        /* ditto */
      }
    })()
    void runQuery(sql, limit, 0, [])
    return () => {
      cancelled = true
    }
    // limit is intentionally not a dependency: changing it re-runs via its own
    // handler; it must not reset the field/order selections. `tables` (via
    // `selected`) is: a database switch refreshes the list and reloads the rows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, selected, runQuery])

  // Order-by changes re-run immediately — browsing wants instant feedback, and
  // the query is the cheap paginated SELECT the server already ran.
  function changeOrder(next: OrderCol[]) {
    setOrderBy(next)
    if (selected) void runQuery(selected.query, limit, offset, next)
  }

  function page(nextOffset: number) {
    if (selected) void runQuery(selected.query, limit, Math.max(0, nextOffset), orderBy)
  }

  // Selecting a table while the sidebar fills the page drops it back to the
  // split view so the rows appear.
  function selectTable(name: string) {
    setSearchParams({ table: name })
    if (sidebar.presetIndex === FULL) setSidebar({ presetIndex: MINIMAL, width: 256 })
  }

  const { columns, rows } = useMemo(
    () => (output !== null ? parseTsv(output) : { columns: [], rows: [] }),
    [output],
  )
  const shownIdx = useMemo(
    () => shownColumnIndices(columns, fields, visibleCols),
    [columns, fields, visibleCols],
  )

  if (!ready) {
    return (
      <div className="w-full max-w-md text-center">
        <h1 className="text-3xl font-bold tracking-tight text-white [text-shadow:0_2px_30px_rgba(129,140,248,0.45)]">
          Explorer
        </h1>
        <p data-testid="explorer-hint" className="mt-4 text-sm text-slate-400">
          Connect and select a database on the Queries page first.
        </p>
      </div>
    )
  }

  const hidden = sidebar.presetIndex === HIDDEN
  const full = sidebar.presetIndex === FULL
  const split = isSplit(sidebar.presetIndex)
  const presetName = ['hidden', 'minimal', 'expanded', 'full'][sidebar.presetIndex]

  // The size-cycle button: lives in the Tables header when the panel shows,
  // and in the rows header when the panel is hidden.
  const cycleButton = (
    <button
      type="button"
      data-testid="explorer-sidebar-cycle"
      data-preset={presetName}
      onClick={() => setSidebar(nextPreset(sidebar))}
      aria-label={`Tables panel: ${presetName} (click to resize)`}
      title={`Tables panel: ${presetName}`}
      className="glass-btn flex h-6 w-6 shrink-0 items-center justify-center p-0 text-slate-300"
    >
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M10 4v16" />
      </svg>
    </button>
  )

  return (
    // mt-10 keeps the panels clear of the absolutely-positioned connection
    // pill (top-left) and nav (top-right) when the content is viewport-tall.
    <div ref={rowRef} className="mt-10 flex w-full max-w-[85vw] items-start gap-2">
      {!hidden && (
      <aside
        data-testid="explorer-tables"
        className={`glass-panel shrink-0 p-4 ${full ? 'min-w-0 flex-1' : ''}`}
        style={full ? undefined : { width: sidebar.width }}
      >
        <div className="flex items-center gap-2">
          {cycleButton}
          <h2 className="text-sm font-semibold text-slate-200">Tables</h2>
        </div>
        {tablesError && (
          <p data-testid="explorer-tables-error" className="mt-2 text-sm text-red-300">
            {tablesError}
          </p>
        )}
        <div className="mt-2 max-h-[70vh] space-y-1 overflow-auto">
          {tables.map((t) => {
            const meta = tableMeta(t)
            return (
              <button
                key={t.name}
                type="button"
                data-testid="explorer-table"
                data-table={t.name}
                onClick={() => selectTable(t.name)}
                className={`flex w-full items-baseline gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-white/10 ${
                  t.name === table
                    ? 'bg-white/10 font-medium text-indigo-200'
                    : 'text-slate-200'
                }`}
              >
                <span className="min-w-0 flex-1 truncate">{t.name}</span>
                {meta && (
                  <span
                    data-testid="explorer-table-meta"
                    className="shrink-0 whitespace-nowrap text-xs font-normal text-slate-400"
                  >
                    {meta}
                  </span>
                )}
              </button>
            )
          })}
          {tables.length === 0 && !tablesError && (
            <p className="text-sm text-slate-400">No tables.</p>
          )}
        </div>
      </aside>
      )}

      {/* Drag divider: only in the split states, between panel and rows. */}
      {split && (
        <div
          data-testid="explorer-sidebar-divider"
          onPointerDown={startResize}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize tables panel"
          className="h-[70vh] w-1.5 shrink-0 cursor-col-resize rounded bg-white/5 hover:bg-white/20"
        />
      )}

      {!full && (
      <section data-testid="explorer-panel" className="glass-panel min-w-0 flex-1 space-y-3 p-6">
        {!table ? (
          <div className="flex items-center gap-2">
            {hidden && cycleButton}
            <p data-testid="explorer-hint" className="text-sm text-slate-400">
              Select a table to browse its rows.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <h2
                data-testid="explorer-table-name"
                className="mr-auto truncate text-lg font-semibold text-white"
              >
                {table}
              </h2>
              {hidden && cycleButton}
              <label className="text-sm text-slate-300">
                Limit
                <input
                  type="number"
                  value={limit}
                  min={1}
                  onChange={(e) => setLimit(Number(e.target.value) || 1)}
                  onBlur={() => {
                    if (limit !== appliedLimit.current) page(0)
                  }}
                  aria-label="Limit"
                  data-testid="explorer-limit"
                  className="glass-input ml-1 w-20 px-3 py-2"
                />
              </label>
              <button
                type="button"
                onClick={() => page(offset - limit)}
                disabled={busy || offset === 0}
                data-testid="explorer-prev"
                className="glass-btn px-3 py-2 text-sm"
              >
                ← Previous
              </button>
              <button
                type="button"
                onClick={() => page(offset + limit)}
                disabled={busy}
                data-testid="explorer-next"
                className="glass-btn px-3 py-2 text-sm"
              >
                Next →
              </button>
              <span data-testid="explorer-offset" className="text-xs text-slate-400">
                offset {offset}
              </span>
            </div>

            {fields.length > 0 && (
              <FieldPickers
                fields={fields}
                visibleCols={visibleCols}
                orderBy={orderBy}
                onVisibleColsChange={setVisibleCols}
                onOrderByChange={changeOrder}
                orderHeaderExtra={
                  <span className="text-xs text-slate-400">(re-runs the query)</span>
                }
              />
            )}

            {output !== null && (
              <ResultsTable
                columns={columns}
                rows={rows}
                shownIdx={shownIdx}
                testid="explorer-output"
              />
            )}
            {error && (
              <p data-testid="explorer-error" className="text-sm text-red-300">
                {error}
              </p>
            )}
          </>
        )}
      </section>
      )}
    </div>
  )
}

export default ExplorerView
