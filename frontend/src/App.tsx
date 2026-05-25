import { useCallback, useEffect, useState } from 'react'

type TestResult = { ok: boolean; message: string }

type Connection = {
  name: string
  type: string
  databases: string[]
  database: string | null
}

type PredefinedQuery = { query_name: string; query: string }

function App() {
  const [prompt, setPrompt] = useState('')
  const [hint, setHint] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [connection, setConnection] = useState<Connection | null>(null)
  const [showQuery, setShowQuery] = useState(false)

  // On load: open a connection named explicitly via ?connection=<name>,
  // otherwise resume the session's last active connection.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('connection')
    if (requested) {
      // Clean the URL so a later reload resumes normally instead of re-opening.
      history.replaceState(null, '', window.location.pathname)
      void openSaved(requested)
      return
    }
    fetch('/api/session')
      .then((r) => r.json())
      .then((s) => {
        if (s.connected) {
          setConnection({
            name: s.name,
            type: s.type ?? 'clickhouse',
            databases: s.databases ?? [],
            database: s.database ?? null,
          })
        }
      })
      .catch(() => {})
  }, [])

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
    if (lower.startsWith('connect ')) {
      const name = raw.slice('connect '.length).trim().split(/\s+/)[0]
      if (name) {
        void openSaved(name)
        return
      }
    }
    setShowForm(false)
    setShowQuery(false)
    setHint(`Unknown command “${raw}”. Try “new clickhouse” or “connect <name>”.`)
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

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-900">
      {connection?.database && (
        <div
          className="absolute left-4 top-4 flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium shadow-sm"
          data-testid="connection-status"
        >
          <span
            className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500"
            data-testid="connection-indicator"
            aria-label="connected"
          />
          connected - {connection.database}
        </div>
      )}

      <div
        className={`w-full ${
          showQuery && connection?.database ? 'max-w-[80vw]' : 'max-w-md'
        }`}
      >
        <h1 className="mb-6 text-center text-3xl font-bold tracking-tight">
          QueryView
        </h1>

        <form onSubmit={submitPrompt}>
          <input
            type="text"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={
              connection?.database
                ? 'query'
                : 'Type a command, e.g. new clickhouse'
            }
            aria-label="Prompt"
            data-testid="prompt-input"
            autoFocus
            className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-center outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200"
          />
        </form>

        {hint && (
          <p
            className="mt-3 text-center text-sm text-slate-500"
            data-testid="prompt-hint"
          >
            {hint}
          </p>
        )}

        {showForm && <ClickHouseForm onConnected={handleConnected} />}

        {!showForm && connection && connection.database === null && (
          <DatabasePicker connection={connection} onSelect={selectDatabase} />
        )}

        {showQuery && connection?.database && (
          <QueryPanel connectionType={connection.type} />
        )}
      </div>
    </main>
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

  const fieldClass =
    'w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200'

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        connect()
      }}
      data-testid="clickhouse-form"
      className="mt-6 space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
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
        <label key={testid} className="block text-sm font-medium text-slate-700">
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
          className="flex-1 rounded-md border border-indigo-600 px-4 py-2 font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:opacity-50"
        >
          Test connection
        </button>
        <button
          type="submit"
          data-testid="ch-connect"
          disabled={busy}
          className="flex-1 rounded-md bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          Connect
        </button>
      </div>

      {result && (
        <p
          data-testid="ch-result"
          data-ok={result.ok}
          className={`text-sm ${result.ok ? 'text-emerald-700' : 'text-red-600'}`}
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
    <section
      data-testid="db-picker"
      className="mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <h2 className="text-sm font-medium text-slate-700">
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
              className={`rounded-md border px-3 py-1.5 text-sm transition ${
                selected
                  ? 'border-indigo-600 bg-indigo-600 text-white'
                  : 'border-slate-300 hover:border-indigo-400 hover:bg-indigo-50'
              }`}
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

function QueryPanel({ connectionType }: { connectionType: string }) {
  const [sql, setSql] = useState('')
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [rows, setRows] = useState(4)
  const [output, setOutput] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [predefined, setPredefined] = useState<PredefinedQuery[]>([])
  const [saveName, setSaveName] = useState('')

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

  async function run(nextOffset: number) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sql, limit, offset: nextOffset, format: 'text' }),
      })
      const data = await res.json()
      if (data.ok) {
        setOutput(data.output as string)
        setOffset(nextOffset)
      } else {
        setError(data.message ?? 'query failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  async function downloadCsv() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sql, limit, offset, format: 'csv' }),
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

  async function save() {
    const name = saveName.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/predefined-queries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query_name: name, type: connectionType, query: sql }),
      })
      const data = await res.json()
      if (data.ok) {
        await loadPredefined()
      } else {
        setError(data.message ?? 'save failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  const { columns, rows: resultRows } =
    output !== null ? parseTsv(output) : { columns: [], rows: [] }

  const sizes: [string, number, string][] = [
    ['S', 4, 'query-size-s'],
    ['M', 8, 'query-size-m'],
    ['L', 16, 'query-size-l'],
    ['XL', 28, 'query-size-xl'],
  ]
  const inputClass =
    'rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200'

  return (
    <section
      data-testid="query-panel"
      className="mt-6 space-y-3 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <select
          data-testid="query-predefined-select"
          aria-label="Predefined queries"
          defaultValue=""
          onChange={(e) => {
            const q = predefined.find((p) => p.query_name === e.target.value)
            if (q) setSql(q.query)
          }}
          className={`min-w-0 flex-1 ${inputClass}`}
        >
          <option value="">Predefined queries…</option>
          {predefined.map((p) => (
            <option key={p.query_name} value={p.query_name}>
              {p.query_name}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
          placeholder="name"
          aria-label="Save query name"
          data-testid="query-save-name"
          className={`w-44 ${inputClass}`}
        />
        <button
          type="button"
          onClick={save}
          disabled={busy}
          data-testid="query-save"
          className="rounded-md border border-indigo-600 px-3 py-2 font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:opacity-50"
        >
          Save
        </button>
      </div>

      <div className="flex justify-end gap-1">
        {sizes.map(([label, n, testid]) => (
          <button
            key={testid}
            type="button"
            onClick={() => setRows(n)}
            data-testid={testid}
            className={`rounded-md border px-2 py-1 text-xs ${
              rows === n
                ? 'border-indigo-600 bg-indigo-600 text-white'
                : 'border-slate-300 hover:bg-slate-50'
            }`}
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
        rows={rows}
        placeholder="SELECT …"
        className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200"
      />

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void run(offset)}
          disabled={busy}
          data-testid="query-run"
          className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          Execute
        </button>
        <label className="text-sm text-slate-700">
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
        <label className="text-sm text-slate-700">
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
          className="rounded-md border border-slate-300 px-3 py-2 text-sm transition hover:bg-slate-50 disabled:opacity-50"
        >
          ← Previous
        </button>
        <button
          type="button"
          onClick={() => void run(offset + limit)}
          disabled={busy}
          data-testid="query-next"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm transition hover:bg-slate-50 disabled:opacity-50"
        >
          Next →
        </button>
        <button
          type="button"
          onClick={downloadCsv}
          disabled={busy}
          data-testid="query-csv"
          className="rounded-md border border-emerald-600 px-3 py-2 text-sm font-medium text-emerald-700 transition hover:bg-emerald-50 disabled:opacity-50"
        >
          Download CSV
        </button>
      </div>

      {output !== null && (
        <div
          data-testid="query-output"
          className="max-h-[70vh] overflow-auto rounded-md border border-slate-200"
        >
          <table className="min-w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 bg-slate-100">
              <tr>
                {columns.map((col, i) => (
                  <th
                    key={i}
                    className="border-b border-slate-300 px-3 py-2 font-semibold text-slate-700"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {resultRows.map((row, i) => (
                <tr key={i} className="odd:bg-white even:bg-slate-50">
                  {row.map((cell, j) => (
                    <td
                      key={j}
                      className="whitespace-pre border-b border-slate-100 px-3 py-1 font-mono text-slate-800"
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error && (
        <p data-testid="query-error" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </section>
  )
}

export default App
