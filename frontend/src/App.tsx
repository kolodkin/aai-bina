import { useEffect, useState } from 'react'

type TestResult = { ok: boolean; message: string }

type Connection = {
  name: string
  databases: string[]
  database: string | null
}

function App() {
  const [prompt, setPrompt] = useState('')
  const [hint, setHint] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [connection, setConnection] = useState<Connection | null>(null)

  // At session start, attempt to resume the latest active connection.
  useEffect(() => {
    fetch('/api/session')
      .then((r) => r.json())
      .then((s) => {
        if (s.connected) {
          setConnection({
            name: s.name,
            databases: s.databases ?? [],
            database: s.database ?? null,
          })
        }
      })
      .catch(() => {})
  }, [])

  function submitPrompt(e: React.FormEvent) {
    e.preventDefault()
    const cmd = prompt.trim().toLowerCase()
    if (!cmd) return
    if (cmd === 'connect clickhouse') {
      setShowForm(true)
      setHint(null)
    } else {
      setShowForm(false)
      setHint(prompt.trim())
    }
  }

  function handleConnected(name: string, databases: string[]) {
    setConnection({ name, databases, database: null })
    setShowForm(false)
    setPrompt('connect clickhouse db')
  }

  async function selectDatabase(database: string) {
    const res = await fetch('/api/clickhouse/database', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ database }),
    })
    if (res.ok) {
      setConnection((c) => (c ? { ...c, database } : c))
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

      <div className="w-full max-w-md">
        <h1 className="mb-6 text-center text-3xl font-bold tracking-tight">
          QueryView
        </h1>

        <form onSubmit={submitPrompt}>
          <input
            type="text"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Type a command, e.g. connect clickhouse"
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
            Unknown command “{hint}”. Try “connect clickhouse”.
          </p>
        )}

        {showForm && <ClickHouseForm onConnected={handleConnected} />}

        {!showForm && connection && connection.database === null && (
          <DatabasePicker connection={connection} onSelect={selectDatabase} />
        )}
      </div>
    </main>
  )
}

function ClickHouseForm({
  onConnected,
}: {
  onConnected: (name: string, databases: string[]) => void
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
        onConnected(data.name as string, (data.databases ?? []) as string[])
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
      <h2 className="text-lg font-semibold">Connect ClickHouse</h2>

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

export default App
