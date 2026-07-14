import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { FieldPickers, type Field, type OrderCol } from './FieldPickers'
import { isReady, type Connection } from './QueryView'
import { formatCompact } from './compactNumber'
import { tableSelectSql } from './explorerSql'
import { parseTsv } from './tsv'

// Sidebar entry from /api/db/tables. rows/bytes are engine estimates; null
// when the engine doesn't track them (views, DuckDB's missing byte size).
type TableInfo = { name: string; rows: number | null; bytes: number | null }

// The "1.2K rows · 3.4M" subline, or null when the engine knows neither.
function tableMeta(t: TableInfo): string | null {
  const parts = [
    t.rows != null ? `${formatCompact(t.rows)} rows` : null,
    t.bytes != null ? formatCompact(t.bytes) : null,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

// The classical table-navigator page (`/explorer?table=x`): a sidebar lists the
// active database's tables; picking one browses its rows with the same field
// select / order-by select as the query panel. The generated SQL is always
// `SELECT * FROM "<table>"` — field selection is client-side column visibility,
// order-by and pagination go to the server.
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
    async (t: string, lim: number, off: number, ord: OrderCol[]) => {
      setBusy(true)
      setError(null)
      try {
        const res = await fetch('/api/db/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: tableSelectSql(t),
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

  // Selecting a table (or switching database with one selected): reset the
  // presentation, describe it for the pickers, and load the first page.
  useEffect(() => {
    if (!ready || !table) return
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
          body: JSON.stringify({ query: tableSelectSql(table) }),
        })
        const data = await res.json()
        if (!cancelled && data.ok) {
          const next = (data.fields ?? []) as Field[]
          setFields(next)
          setVisibleCols(next.map((f) => f.name))
        }
        // A failed describe isn't fatal: the run below reports the real error.
      } catch {
        /* ditto */
      }
      if (!cancelled) void runQuery(table, limit, 0, [])
    })()
    return () => {
      cancelled = true
    }
    // limit is intentionally not a dependency: changing it re-runs via its own
    // handler; it must not reset the field/order selections.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, table, database, runQuery])

  // Order-by changes re-run immediately — browsing wants instant feedback, and
  // the query is the cheap paginated SELECT the server already ran.
  function changeOrder(next: OrderCol[]) {
    setOrderBy(next)
    if (table) void runQuery(table, limit, offset, next)
  }

  function page(nextOffset: number) {
    if (table) void runQuery(table, limit, Math.max(0, nextOffset), orderBy)
  }

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

  const { columns, rows } = output !== null ? parseTsv(output) : { columns: [], rows: [] }
  // Same stale-safety as the query panel: a column the describe didn't cover
  // always shows, so the visibility filter can't blank the table.
  const fieldNames = new Set(fields.map((f) => f.name))
  const visible = new Set(visibleCols)
  const shownIdx = columns
    .map((_, i) => i)
    .filter((i) => !fieldNames.has(columns[i]) || visible.has(columns[i]))

  return (
    <div className="flex w-full max-w-[85vw] items-start gap-4">
      <aside data-testid="explorer-tables" className="glass-panel w-64 shrink-0 p-4">
        <h2 className="text-sm font-semibold text-slate-200">Tables</h2>
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
                onClick={() => setSearchParams({ table: t.name })}
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

      <section data-testid="explorer-panel" className="glass-panel min-w-0 flex-1 space-y-3 p-6">
        {!table ? (
          <p data-testid="explorer-hint" className="text-sm text-slate-400">
            Select a table to browse its rows.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <h2
                data-testid="explorer-table-name"
                className="mr-auto truncate text-lg font-semibold text-white"
              >
                {table}
              </h2>
              <label className="text-sm text-slate-300">
                Limit
                <input
                  type="number"
                  value={limit}
                  min={1}
                  onChange={(e) => setLimit(Number(e.target.value) || 1)}
                  onBlur={() => page(0)}
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
              <div
                data-testid="explorer-output"
                className="max-h-[65vh] overflow-auto rounded-xl border border-white/10"
              >
                <table className="min-w-full border-collapse text-left text-sm">
                  <thead className="sticky top-0 bg-[rgba(16,20,36,0.62)] backdrop-blur-lg">
                    <tr>
                      {shownIdx.map((i) => (
                        <th
                          key={i}
                          className="border-b border-white/10 px-3 py-2 font-semibold text-slate-200"
                        >
                          {columns[i]}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={i} className="odd:bg-transparent even:bg-white/[0.03]">
                        {shownIdx.map((j) => (
                          <td
                            key={j}
                            className="whitespace-pre border-b border-white/5 px-3 py-1 font-mono text-slate-200"
                          >
                            {row[j]}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {error && (
              <p data-testid="explorer-error" className="text-sm text-red-300">
                {error}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  )
}

export default ExplorerView
