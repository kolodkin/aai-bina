import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import yaml from 'js-yaml'

import { CellViewModal } from './CellViewModal'

type TestResult = { ok: boolean; message: string }

export type Connection = {
  name: string
  type: string
  databases: string[]
  database: string | null
}

type PredefinedQuery = { query_name: string; query: string; cell_view: string | null }

type CellView = { type: string; value: string }
type CellViewMap = Record<string, CellView>

type Field = { name: string; type: string }

type OrderCol = { name: string; dir: 'ASC' | 'DESC' }

export type QueryPush = {
  query: string
  limit?: number
  offset?: number
  order_by?: OrderCol[]
  fields?: string[]
}

// Sentinel value for the predefined dropdown's "new name" item.
const NEW_NAME_OPTION = '::new::'

// The query workflow page (`/queries`). Connection state and the persistent
// connection pill + agent popover live in the App shell; this page owns the
// command prompt and the connect / pick-database / query UI.
function QueryView({
  connection,
  setConnection,
  pushed,
  onPushConsumed,
}: {
  connection: Connection | null
  setConnection: (c: Connection | null) => void
  pushed?: QueryPush | null
  onPushConsumed?: () => void
}) {
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const [hint, setHint] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [showQuery, setShowQuery] = useState(false)

  // A pushed query arrives via the shell's SSE listener; make sure the panel is
  // mounted so it can reflect and auto-run it.
  useEffect(() => {
    if (pushed && connection?.database) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowQuery(true)
    }
  }, [pushed, connection?.database])

  async function openSaved(name: string) {
    try {
      const res = await fetch('/api/clickhouse/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      const data = await res.json()
      if (!data.ok) {
        setHint(data.message ?? `no connection named “${name}”`)
        return
      }
      setShowForm(false)
      setShowQuery(false)
      setHint(null)
      setConnection({
        name: data.name,
        type: (data.type ?? 'clickhouse') as string,
        databases: (data.databases ?? []) as string[],
        database: null,
      })
      setPrompt(`connect ${data.name}`)
    } catch (err) {
      setHint(err instanceof Error ? err.message : 'request failed')
    }
  }

  function submitPrompt(e: React.FormEvent) {
    e.preventDefault()
    const raw = prompt.trim()
    if (!raw) return
    const lower = raw.toLowerCase()
    if (lower === 'new clickhouse') {
      setShowForm(true)
      setShowQuery(false)
      setHint(null)
      return
    }
    if (lower === 'query') {
      if (connection?.database) {
        setShowQuery(true)
        setShowForm(false)
        setHint(null)
      } else {
        setHint('Select a database first.')
      }
      return
    }
    if (lower === 'dashboard') {
      navigate('/dashboard')
      return
    }
    if (lower.startsWith('dashboard ')) {
      const name = raw.slice('dashboard '.length).trim().split(/\s+/)[0]
      if (name) {
        navigate(`/dashboard?name=${encodeURIComponent(name)}`)
        return
      }
    }
    if (lower.startsWith('connect ')) {
      const name = raw.slice('connect '.length).trim().split(/\s+/)[0]
      if (name) {
        void openSaved(name)
        return
      }
    }
    setShowForm(false)
    setShowQuery(false)
    setHint(
      `Unknown command “${raw}”. Try “new clickhouse”, “connect <name>” or “dashboard <name>”.`,
    )
  }

  function handleConnected(name: string, type: string, databases: string[]) {
    setConnection({ name, type, databases, database: null })
    setShowForm(false)
    setShowQuery(false)
    setPrompt(`connect ${name}`)
  }

  async function selectDatabase(database: string) {
    if (!connection) return
    const res = await fetch('/api/clickhouse/database', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ database }),
    })
    if (res.ok) {
      setConnection({ ...connection, database })
      // Database chosen — clear the prompt; the placeholder now invites a query.
      setPrompt('')
    }
  }

  const inQueryMode = showQuery && Boolean(connection?.database)

  // The command prompt. In query mode it joins the panel's top row (alongside the
  // predefined-query controls) instead of standing on its own, to save vertical space.
  const promptInput = (
    <form onSubmit={submitPrompt} className={inQueryMode ? 'min-w-0 flex-1' : undefined}>
      <input
        type="text"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder={
          connection?.database ? 'query' : 'Type a command, e.g. new clickhouse'
        }
        aria-label="Prompt"
        data-testid="prompt-input"
        autoFocus
        className={
          inQueryMode
            ? 'glass-input w-full px-3 py-2 text-sm'
            : 'glass-input w-full px-4 py-3 text-center'
        }
      />
    </form>
  )

  return (
    <div className={`w-full ${inQueryMode ? 'max-w-[80vw]' : 'max-w-md'}`}>
      <h1 className="mb-6 text-center text-3xl font-bold tracking-tight text-white [text-shadow:0_2px_30px_rgba(129,140,248,0.45)]">
        QueryView
      </h1>

      {!inQueryMode && promptInput}

      {hint && (
        <p className="mt-3 text-center text-sm text-slate-400" data-testid="prompt-hint">
          {hint}
        </p>
      )}

      {showForm && <ClickHouseForm onConnected={handleConnected} />}

      {!showForm && connection && connection.database === null && (
        <DatabasePicker connection={connection} onSelect={selectDatabase} />
      )}

      {showQuery && connection?.database && (
        <QueryPanel
          connectionType={connection.type}
          promptSlot={promptInput}
          pushed={pushed}
          onPushConsumed={onPushConsumed}
        />
      )}
    </div>
  )
}

function ClickHouseForm({
  onConnected,
}: {
  onConnected: (name: string, type: string, databases: string[]) => void
}) {
  const [name, setName] = useState('clickhouse')
  const [host, setHost] = useState('localhost')
  const [port, setPort] = useState('8123')
  const [username, setUsername] = useState('default')
  const [password, setPassword] = useState('')
  const [result, setResult] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)

  function body() {
    return JSON.stringify({ name, host, port: Number(port), username, password })
  }

  async function testConnection() {
    setBusy(true)
    setResult(null)
    try {
      const res = await fetch('/api/clickhouse/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body(),
      })
      setResult((await res.json()) as TestResult)
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : 'request failed' })
    } finally {
      setBusy(false)
    }
  }

  async function connect() {
    setBusy(true)
    setResult(null)
    try {
      const res = await fetch('/api/clickhouse/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body(),
      })
      const data = await res.json()
      if (data.ok) {
        onConnected(
          data.name as string,
          (data.type ?? 'clickhouse') as string,
          (data.databases ?? []) as string[],
        )
      } else {
        setResult({ ok: false, message: data.message ?? 'connect failed' })
      }
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : 'request failed' })
    } finally {
      setBusy(false)
    }
  }

  const fieldClass = 'glass-input w-full px-3 py-2'

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        connect()
      }}
      data-testid="clickhouse-form"
      className="glass-panel mt-6 space-y-4 p-6"
    >
      <h2 className="text-lg font-semibold">New ClickHouse connection</h2>

      {(
        [
          ['Name', name, setName, 'ch-name', 'text'],
          ['Host', host, setHost, 'ch-host', 'text'],
          ['Port', port, setPort, 'ch-port', 'text'],
          ['Username', username, setUsername, 'ch-username', 'text'],
          ['Password', password, setPassword, 'ch-password', 'password'],
        ] as const
      ).map(([label, value, setter, testid, type]) => (
        <label key={testid} className="block text-sm font-medium text-slate-300">
          {label}
          <input
            type={type}
            value={value}
            onChange={(e) => setter(e.target.value)}
            aria-label={label}
            data-testid={testid}
            className={`mt-1 ${fieldClass}`}
          />
        </label>
      ))}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={testConnection}
          data-testid="ch-test"
          disabled={busy}
          className="glass-btn flex-1 px-4 py-2 font-medium"
        >
          Test connection
        </button>
        <button
          type="submit"
          data-testid="ch-connect"
          disabled={busy}
          className="glass-btn-primary flex-1 px-4 py-2 font-medium"
        >
          Connect
        </button>
      </div>

      {result && (
        <p
          data-testid="ch-result"
          data-ok={result.ok}
          className={`text-sm ${result.ok ? 'text-emerald-300' : 'text-red-300'}`}
        >
          {result.message}
        </p>
      )}
    </form>
  )
}

function DatabasePicker({
  connection,
  onSelect,
}: {
  connection: Connection
  onSelect: (database: string) => void
}) {
  return (
    <section data-testid="db-picker" className="glass-panel mt-6 p-6">
      <h2 className="text-sm font-medium text-slate-200">
        Connected to {connection.name}. Select a database:
      </h2>
      <div className="mt-3 flex flex-wrap gap-2">
        {connection.databases.map((db) => {
          const selected = db === connection.database
          return (
            <button
              key={db}
              type="button"
              onClick={() => onSelect(db)}
              data-testid="db-option"
              data-db={db}
              className={`glass-toggle px-3 py-1.5 text-sm ${selected ? 'is-active' : ''}`}
            >
              {db}
            </button>
          )
        })}
      </div>
    </section>
  )
}

function parseTsv(text: string): { columns: string[]; rows: string[][] } {
  // TabSeparatedWithNames: the first line is the column names, the rest are rows.
  if (text === '') return { columns: [], rows: [] }
  const lines = text.split('\n')
  return { columns: lines[0].split('\t'), rows: lines.slice(1).map((l) => l.split('\t')) }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// Parse the saved cell_view YAML into a map. A parse error, a non-mapping
// root, or any entry without a string {type, value} is dropped — a broken
// config never blanks the table; it just falls through to plain text.
function parseCellViewYaml(text: string | null | undefined): CellViewMap {
  if (!text) return {}
  let doc: unknown
  try {
    doc = yaml.load(text)
  } catch {
    return {}
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return {}
  const out: CellViewMap = {}
  for (const [k, v] of Object.entries(doc as Record<string, unknown>)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const o = v as Record<string, unknown>
      if (typeof o.type === 'string' && typeof o.value === 'string') {
        out[k] = { type: o.type, value: o.value }
      }
    }
  }
  return out
}

function renderCell(colName: string, raw: string, views: CellViewMap): React.ReactNode {
  const view = views[colName]
  if (!view) return raw
  const testid = `cell-${colName}`
  if (view.type === 'link') {
    const href = view.value.replaceAll('{cell}', encodeURIComponent(raw))
    let scheme: string
    try {
      scheme = new URL(href).protocol
    } catch {
      return raw
    }
    if (scheme !== 'http:' && scheme !== 'https:') return raw
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        data-testid={testid}
        className="text-indigo-300 underline hover:text-indigo-200"
      >
        {raw}
      </a>
    )
  }
  if (view.type === 'custom') {
    const html = view.value.replaceAll('{cell}', escapeHtml(raw))
    // Cell value is HTML-escaped above so DB content is inert; the template
    // HTML is trusted (anyone who can save a predefined query can inject markup
    // for all viewers — documented in docs/query.md).
    return <span data-testid={testid} dangerouslySetInnerHTML={{ __html: html }} />
  }
  return raw
}

function QueryPanel({
  connectionType,
  promptSlot,
  pushed,
  onPushConsumed,
}: {
  connectionType: string
  promptSlot?: React.ReactNode
  pushed?: QueryPush | null
  onPushConsumed?: () => void
}) {
  const [sql, setSql] = useState('')
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [rows, setRows] = useState(4)
  const [output, setOutput] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [predefined, setPredefined] = useState<PredefinedQuery[]>([])
  const [selectedName, setSelectedName] = useState('')
  const [fields, setFields] = useState<Field[]>([])
  const [visibleCols, setVisibleCols] = useState<string[]>([])
  const [orderBy, setOrderBy] = useState<OrderCol[]>([])
  const [cellViewModalOpen, setCellViewModalOpen] = useState(false)

  // The saved cell_view of the currently selected predefined query, or '' when
  // nothing is selected. Single source of truth for rendering, modal seeding,
  // and re-saves of SQL through the top "Save" button.
  const savedCellView = useMemo(
    () => predefined.find((p) => p.query_name === selectedName)?.cell_view ?? '',
    [predefined, selectedName],
  )

  // Editor edits don't take effect until Save (which refreshes `predefined`).
  const appliedViews = useMemo<CellViewMap>(
    () => parseCellViewYaml(savedCellView),
    [savedCellView],
  )

  const loadPredefined = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/predefined-queries?type=${encodeURIComponent(connectionType)}`,
      )
      const data = await res.json()
      setPredefined((data.queries ?? []) as PredefinedQuery[])
    } catch {
      // a missing list is non-fatal; leave the selector empty
    }
  }, [connectionType])

  useEffect(() => {
    // setPredefined runs after the fetch await, so it doesn't cascade renders.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPredefined()
  }, [loadPredefined])

  // Apply a pushed query: reflect it in the controls and run it with the pushed
  // values directly (not state, which hasn't settled yet).
  useEffect(() => {
    if (!pushed) return
    const q = pushed.query
    const lim = pushed.limit ?? 100
    const off = pushed.offset ?? 0
    const ord = pushed.order_by ?? []
    const fld = pushed.fields ?? []
    /* eslint-disable react-hooks/set-state-in-effect */
    setSql(q)
    setLimit(lim)
    setOffset(off)
    setOrderBy(ord)
    /* eslint-enable react-hooks/set-state-in-effect */
    void runWith(q, lim, off, ord, fld)
    // Consume the push so re-mounting the panel doesn't re-run a stale query.
    onPushConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushed])

  async function describe() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/describe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sql }),
      })
      const data = await res.json()
      if (data.ok) {
        const next = (data.fields ?? []) as Field[]
        setFields(next)
        // Default to all columns visible; drop order-by entries no longer present.
        setVisibleCols(next.map((f) => f.name))
        setOrderBy((prev) => prev.filter((o) => next.some((f) => f.name === o.name)))
      } else {
        setError(data.message ?? 'describe failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  async function runWith(
    q: string,
    lim: number,
    off: number,
    ord: OrderCol[],
    selectFields?: string[],
  ) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, limit: lim, offset: off, format: 'text', order_by: ord }),
      })
      const data = await res.json()
      if (data.ok) {
        const text = data.output as string
        setOutput(text)
        setOffset(off)
        // A pushed selection is authoritative: synthesize the field list from
        // the actual result columns so the existing visibility filter restricts
        // the table to exactly the pushed columns (empty/absent => show all).
        if (selectFields !== undefined) {
          const cols = parseTsv(text).columns
          setFields(cols.map((name) => ({ name, type: '' })))
          setVisibleCols(
            selectFields.length ? selectFields.filter((f) => cols.includes(f)) : cols,
          )
        }
      } else {
        setError(data.message ?? 'query failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  function run(nextOffset: number) {
    void runWith(sql, limit, nextOffset, orderBy)
  }

  async function downloadCsv() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sql, limit, offset, format: 'csv', order_by: orderBy }),
      })
      const data = await res.json()
      if (!data.ok) {
        setError(data.message ?? 'query failed')
        return
      }
      const blob = new Blob([data.output as string], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'query.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  // Dropdown selection: a saved query loads its SQL and cell_view; the
  // "new name" item prompts for a fresh name. Either way the chosen name is
  // what Save writes under.
  function onSelectName(value: string) {
    if (value === NEW_NAME_OPTION) {
      const name = window.prompt('Save query as (name):', selectedName || '')?.trim()
      if (name) setSelectedName(name)
      return
    }
    setSelectedName(value)
    const q = predefined.find((p) => p.query_name === value)
    if (q) setSql(q.query)
  }

  // SQL-only saves (the top button) re-persist the existing cell_view as-is;
  // the modal passes its draft. Returns success so the modal can close only on
  // a clean persist.
  async function save(cellViewValue: string = savedCellView): Promise<boolean> {
    const name = selectedName.trim()
    if (!name) return false
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/predefined-queries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query_name: name,
          type: connectionType,
          query: sql,
          cell_view: cellViewValue,
        }),
      })
      const data = await res.json()
      if (data.ok) {
        await loadPredefined()
        return true
      }
      setError(data.message ?? 'save failed')
      return false
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
      return false
    } finally {
      setBusy(false)
    }
  }

  // Modal owns its draft state (seeded from savedCellView on mount); App just
  // toggles visibility and forwards the saved value to the backend.
  async function onCellViewSave(value: string) {
    if (await save(value)) setCellViewModalOpen(false)
  }

  const { columns, rows: resultRows } =
    output !== null ? parseTsv(output) : { columns: [], rows: [] }

  // Filter table columns by client-side visibility. Columns not among the
  // described fields (e.g. SQL edited since the last describe) always show, so a
  // stale field list can't blank the table.
  const fieldNames = new Set(fields.map((f) => f.name))
  const visible = new Set(visibleCols)
  const shownIdx = columns
    .map((_, i) => i)
    .filter((i) => !fieldNames.has(columns[i]) || visible.has(columns[i]))

  function toggleField(name: string) {
    setVisibleCols((prev) =>
      prev.includes(name) ? prev.filter((c) => c !== name) : [...prev, name],
    )
  }

  function toggleOrder(name: string) {
    setOrderBy((prev) =>
      prev.some((o) => o.name === name)
        ? prev.filter((o) => o.name !== name)
        : [...prev, { name, dir: 'ASC' }],
    )
  }

  function flipDir(name: string) {
    setOrderBy((prev) =>
      prev.map((o) =>
        o.name === name ? { ...o, dir: o.dir === 'ASC' ? 'DESC' : 'ASC' } : o,
      ),
    )
  }

  const sizes: [string, number, string][] = [
    ['Min', 0, 'query-size-min'],
    ['S', 4, 'query-size-s'],
    ['M', 8, 'query-size-m'],
    ['L', 16, 'query-size-l'],
    ['XL', 28, 'query-size-xl'],
  ]
  const inputClass = 'glass-input px-3 py-2'

  return (
    <section data-testid="query-panel" className="glass-panel mt-6 space-y-3 p-6">
      <div className="flex items-center gap-2">
        {promptSlot}
        <select
          data-testid="query-predefined-select"
          aria-label="Predefined queries"
          value={selectedName}
          onChange={(e) => onSelectName(e.target.value)}
          className={`min-w-0 flex-1 ${inputClass}`}
        >
          <option value="">Predefined queries…</option>
          <option value={NEW_NAME_OPTION}>+ New name…</option>
          {selectedName !== '' &&
            !predefined.some((p) => p.query_name === selectedName) && (
              <option value={selectedName}>{selectedName}</option>
            )}
          {predefined.map((p) => (
            <option key={p.query_name} value={p.query_name}>
              {p.query_name}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy || !selectedName.trim()}
          data-testid="query-save"
          className="glass-btn px-3 py-2 font-medium"
        >
          Save
        </button>
      </div>

      <div className="flex justify-end gap-1">
        <button
          type="button"
          onClick={() => setCellViewModalOpen(true)}
          data-testid="cell-view-toggle"
          className="glass-toggle mr-2 px-2 py-1 text-xs"
        >
          Cell view
        </button>
        {sizes.map(([label, n, testid]) => (
          <button
            key={testid}
            type="button"
            onClick={() => setRows(n)}
            data-testid={testid}
            className={`glass-toggle px-2 py-1 text-xs ${rows === n ? 'is-active' : ''}`}
          >
            {label}
          </button>
        ))}
      </div>

      <textarea
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        aria-label="SQL query"
        data-testid="query-input"
        rows={rows || 1}
        placeholder="SELECT …"
        className={`glass-input w-full px-3 font-mono text-sm ${
          rows === 0 ? 'h-0 min-h-0 overflow-hidden border-transparent py-0' : 'py-2'
        }`}
      />

      {cellViewModalOpen && (
        <CellViewModal
          initial={savedCellView}
          onCancel={() => setCellViewModalOpen(false)}
          onSave={(value) => void onCellViewSave(value)}
          saveDisabled={busy || !selectedName.trim()}
        />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void run(offset)}
          disabled={busy}
          data-testid="query-run"
          className="glass-btn-primary px-4 py-2 font-medium"
        >
          Execute
        </button>
        <button
          type="button"
          onClick={() => void describe()}
          disabled={busy}
          data-testid="query-fields"
          className={`px-3 py-2 text-sm font-medium ${
            fields.length > 0 ? 'glass-btn-primary' : 'glass-btn'
          }`}
        >
          Fields
        </button>
        <label className="text-sm text-slate-300">
          Limit
          <input
            type="number"
            value={limit}
            min={1}
            onChange={(e) => setLimit(Number(e.target.value) || 1)}
            aria-label="Limit"
            data-testid="query-limit"
            className={`ml-1 w-20 ${inputClass}`}
          />
        </label>
        <label className="text-sm text-slate-300">
          Offset
          <input
            type="number"
            value={offset}
            min={0}
            onChange={(e) => setOffset(Number(e.target.value) || 0)}
            aria-label="Offset"
            data-testid="query-offset"
            className={`ml-1 w-20 ${inputClass}`}
          />
        </label>
        <button
          type="button"
          onClick={() => void run(Math.max(0, offset - limit))}
          disabled={busy || offset === 0}
          data-testid="query-prev"
          className="glass-btn px-3 py-2 text-sm"
        >
          ← Previous
        </button>
        <button
          type="button"
          onClick={() => void run(offset + limit)}
          disabled={busy}
          data-testid="query-next"
          className="glass-btn px-3 py-2 text-sm"
        >
          Next →
        </button>
        <button
          type="button"
          onClick={downloadCsv}
          disabled={busy}
          data-testid="query-csv"
          className="glass-btn px-3 py-2 text-sm font-medium text-emerald-300"
        >
          Download CSV
        </button>
      </div>

      {fields.length > 0 && (
        <div
          data-testid="field-pickers"
          className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-3"
        >
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-medium text-slate-200">Select fields</span>
              <button
                type="button"
                data-testid="fields-select-all"
                onClick={() => setVisibleCols(fields.map((f) => f.name))}
                className="glass-btn px-2 py-0.5 text-xs"
              >
                Select all
              </button>
              <button
                type="button"
                data-testid="fields-clear"
                onClick={() => setVisibleCols([])}
                className="glass-btn px-2 py-0.5 text-xs"
              >
                Clear all
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {fields.map((f) => {
                const on = visibleCols.includes(f.name)
                return (
                  <button
                    key={f.name}
                    type="button"
                    onClick={() => toggleField(f.name)}
                    data-testid="field-toggle"
                    data-col={f.name}
                    data-on={on}
                    title={f.type}
                    className={`glass-toggle px-2.5 py-1 text-xs ${on ? 'is-active' : ''}`}
                  >
                    {f.name}
                  </button>
                )
              })}
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-medium text-slate-200">Order by</span>
              <button
                type="button"
                data-testid="orderby-run"
                onClick={() => void run(offset)}
                disabled={busy}
                className="glass-btn px-2 py-0.5 text-xs font-medium text-indigo-200"
              >
                Run
              </button>
              <span className="text-xs text-slate-400">(re-runs the query)</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {fields.map((f) => {
                const on = orderBy.some((o) => o.name === f.name)
                return (
                  <button
                    key={f.name}
                    type="button"
                    onClick={() => toggleOrder(f.name)}
                    data-testid="orderby-add"
                    data-col={f.name}
                    data-on={on}
                    className={`glass-toggle px-2.5 py-1 text-xs ${on ? 'is-active-soft' : ''}`}
                  >
                    {f.name}
                  </button>
                )
              })}
            </div>
            {orderBy.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {orderBy.map((o, i) => (
                  <span
                    key={o.name}
                    data-testid="orderby-chip"
                    data-col={o.name}
                    className="flex items-center gap-1 rounded-md border border-indigo-400/40 bg-white/[0.06] px-2 py-1 text-xs"
                  >
                    <span className="text-slate-400">{i + 1}.</span>
                    <span className="font-medium">{o.name}</span>
                    <button
                      type="button"
                      data-testid="orderby-dir"
                      onClick={() => flipDir(o.name)}
                      className="rounded bg-white/10 px-1.5 py-0.5 font-mono hover:bg-white/20"
                    >
                      {o.dir}
                    </button>
                    <button
                      type="button"
                      data-testid="orderby-remove"
                      onClick={() => toggleOrder(o.name)}
                      aria-label={`remove ${o.name}`}
                      className="text-slate-400 hover:text-red-400"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {output !== null && (
        <div
          data-testid="query-output"
          className="max-h-[70vh] overflow-auto rounded-xl border border-white/10"
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
              {resultRows.map((row, i) => (
                <tr key={i} className="odd:bg-transparent even:bg-white/[0.03]">
                  {shownIdx.map((j) => (
                    <td
                      key={j}
                      className="whitespace-pre border-b border-white/5 px-3 py-1 font-mono text-slate-200"
                    >
                      {renderCell(columns[j], row[j], appliedViews)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error && (
        <p data-testid="query-error" className="text-sm text-red-300">
          {error}
        </p>
      )}
    </section>
  )
}

export default QueryView
