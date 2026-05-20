import { useState } from 'react'

type TestResult = { ok: boolean; message: string }

type Connection = { name: string }

function App() {
  const [prompt, setPrompt] = useState('')
  const [showClickHouse, setShowClickHouse] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const [connection, setConnection] = useState<Connection | null>(null)

  function submitPrompt(e: React.FormEvent) {
    e.preventDefault()
    const cmd = prompt.trim().toLowerCase()
    if (!cmd) return
    if (cmd === 'connect clickhouse') {
      setShowClickHouse(true)
      setHint(null)
    } else {
      setShowClickHouse(false)
      setHint(prompt.trim())
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-900">
      {connection && (
        <div
          className="absolute left-4 top-4 flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium shadow-sm"
          data-testid="connection-status"
        >
          <span
            className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500"
            data-testid="connection-indicator"
            aria-label="connected"
          />
          {connection.name}
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

        {showClickHouse && (
          <ClickHouseForm onConnected={(name) => setConnection({ name })} />
        )}
      </div>
    </main>
  )
}

function ClickHouseForm({ onConnected }: { onConnected: (name: string) => void }) {
  const [name, setName] = useState('clickhouse')
  const [host, setHost] = useState('localhost')
  const [port, setPort] = useState('8123')
  const [username, setUsername] = useState('default')
  const [password, setPassword] = useState('')
  const [result, setResult] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)

  async function testConnection(e: React.FormEvent) {
    e.preventDefault()
    setTesting(true)
    setResult(null)
    try {
      const res = await fetch('/api/clickhouse/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, port: Number(port), username, password }),
      })
      const data = (await res.json()) as TestResult
      setResult(data)
      if (data.ok) onConnected(name.trim() || 'clickhouse')
    } catch (err) {
      setResult({
        ok: false,
        message: err instanceof Error ? err.message : 'request failed',
      })
    } finally {
      setTesting(false)
    }
  }

  const fieldClass =
    'w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200'

  return (
    <form
      onSubmit={testConnection}
      data-testid="clickhouse-form"
      className="mt-6 space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <h2 className="text-lg font-semibold">Connect ClickHouse</h2>

      <label className="block text-sm font-medium text-slate-700">
        Name
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Name"
          data-testid="ch-name"
          className={`mt-1 ${fieldClass}`}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Host
        <input
          type="text"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          aria-label="Host"
          data-testid="ch-host"
          className={`mt-1 ${fieldClass}`}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Port
        <input
          type="text"
          inputMode="numeric"
          value={port}
          onChange={(e) => setPort(e.target.value)}
          aria-label="Port"
          data-testid="ch-port"
          className={`mt-1 ${fieldClass}`}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Username
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          aria-label="Username"
          data-testid="ch-username"
          className={`mt-1 ${fieldClass}`}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-label="Password"
          data-testid="ch-password"
          className={`mt-1 ${fieldClass}`}
        />
      </label>

      <button
        type="submit"
        data-testid="ch-test"
        disabled={testing}
        className="w-full rounded-md bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
      >
        {testing ? 'Testing…' : 'Test connection'}
      </button>

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

export default App
