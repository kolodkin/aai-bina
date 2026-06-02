import { useEffect, useMemo, useState } from 'react'
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom'

import QueryView, { type Connection, type QueryPush } from './QueryView'
import DashboardView, { type DashboardPush } from './DashboardView'

// The app shell: client-side routing, the connection state shared by every
// page, the persistent connection pill + agent popover, and the armed / SSE
// remote-control channel. Pages: /queries (the query workflow) and /dashboard.
function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const [connection, setConnection] = useState<Connection | null>(null)
  const [armed, setArmed] = useState(false)
  const [remoteId, setRemoteId] = useState<string | null>(null)
  const [agentOpen, setAgentOpen] = useState(false)
  const [queryPush, setQueryPush] = useState<QueryPush | null>(null)
  const [dashboardPush, setDashboardPush] = useState<DashboardPush | null>(null)

  // The ?connection= deep-link, captured once before any redirect rewrites the
  // URL (the `/` route immediately navigates to `/queries`).
  const initialConnection = useMemo(
    () => new URLSearchParams(window.location.search).get('connection'),
    [],
  )

  async function openConnection(name: string) {
    try {
      const res = await fetch('/api/clickhouse/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      const data = await res.json()
      if (data.ok) {
        setConnection({
          name: data.name,
          type: (data.type ?? 'clickhouse') as string,
          databases: (data.databases ?? []) as string[],
          database: null,
        })
      }
    } catch {
      /* a failed deep-link open just leaves us disconnected */
    }
    navigate('/queries')
  }

  // On load: open a connection named explicitly via ?connection=<name>,
  // otherwise resume the session's last active connection.
  useEffect(() => {
    if (initialConnection) {
      // Async: state is set after the open round-trips, not synchronously here.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void openConnection(initialConnection)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // When armed, open an SSE channel: `ready` gives this session's id; `query`
  // and `dashboard` events carry pushed payloads that navigate to the matching
  // page and hand the payload to it.
  useEffect(() => {
    if (!armed) return
    const es = new EventSource('/api/remote/events')
    es.addEventListener('ready', (e) => {
      try {
        setRemoteId(JSON.parse((e as MessageEvent).data).id as string)
      } catch {
        /* ignore malformed event */
      }
    })
    es.addEventListener('query', (e) => {
      try {
        setQueryPush(JSON.parse((e as MessageEvent).data) as QueryPush)
        navigate('/queries')
      } catch {
        /* ignore malformed event */
      }
    })
    es.addEventListener('dashboard', (e) => {
      try {
        const payload = JSON.parse((e as MessageEvent).data) as DashboardPush
        setDashboardPush(payload)
        navigate(`/dashboard?name=${encodeURIComponent(payload.name)}`)
      } catch {
        /* ignore malformed event */
      }
    })
    return () => {
      es.close()
      setRemoteId(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [armed])

  function toggleArm(e: React.ChangeEvent<HTMLInputElement>) {
    setArmed(e.target.checked)
  }

  const agentCommand = `Use the queryview MCP to push queries to QueryView session "${remoteId ?? ''}".`

  const navLinkClass = (path: string) =>
    `glass-toggle px-3 py-1.5 text-sm ${
      location.pathname.startsWith(path) ? 'is-active' : ''
    }`

  return (
    <main className="relative flex min-h-screen items-center justify-center px-6 py-10 text-slate-100">
      {connection?.database && (
        <div className="absolute left-4 top-4 flex items-center gap-2">
          <div
            className="glass-chip flex items-center gap-2 px-3 py-1.5 text-sm font-medium"
            data-testid="connection-status"
          >
            <span
              className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500"
              data-testid="connection-indicator"
              aria-label="connected"
            />
            connected - {connection.database}
          </div>
          <div className="relative">
            <button
              type="button"
              data-testid="agent-toggle"
              onClick={() => setAgentOpen((o) => !o)}
              aria-label="Remote control"
              className={`flex h-8 w-8 items-center justify-center rounded-full transition ${
                armed ? 'glass-btn-primary' : 'glass-btn text-slate-300'
              }`}
            >
              <svg
                viewBox="0 0 24 24"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="4" y="8" width="16" height="11" rx="2" />
                <path d="M12 8V4M9 3h6" />
                <circle cx="9" cy="13" r="1" />
                <circle cx="15" cy="13" r="1" />
              </svg>
            </button>
            {agentOpen && (
              <div
                data-testid="agent-panel"
                className="glass-popover absolute left-0 top-full z-10 mt-2 w-72 p-3 text-sm"
              >
                <label className="flex items-center gap-2 font-medium text-slate-200">
                  <input
                    type="checkbox"
                    data-testid="remote-arm"
                    checked={armed}
                    onChange={toggleArm}
                  />
                  Allow remote control
                </label>
                {armed && remoteId && (
                  <div className="mt-3 space-y-2">
                    <div className="text-xs text-slate-400">Session id</div>
                    <code
                      data-testid="remote-session-id"
                      className="block rounded bg-white/10 px-2 py-1 font-mono text-slate-100"
                    >
                      {remoteId}
                    </code>
                    <button
                      type="button"
                      data-testid="remote-copy"
                      onClick={() => void navigator.clipboard?.writeText(agentCommand)}
                      className="glass-btn px-2 py-1 text-xs font-medium text-indigo-200"
                    >
                      Copy agent command
                    </button>
                    <p className="text-xs text-slate-400">{agentCommand}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <nav className="absolute right-4 top-4 flex gap-2" data-testid="nav">
        <Link to="/queries" data-testid="nav-queries" className={navLinkClass('/queries')}>
          Queries
        </Link>
        <Link
          to="/dashboard"
          data-testid="nav-dashboard"
          className={navLinkClass('/dashboard')}
        >
          Dashboard
        </Link>
      </nav>

      <Routes>
        <Route
          path="/queries"
          element={
            <QueryView
              connection={connection}
              setConnection={setConnection}
              pushed={queryPush}
              onPushConsumed={() => setQueryPush(null)}
            />
          }
        />
        <Route
          path="/dashboard"
          element={
            <DashboardView
              pushed={dashboardPush}
              onPushConsumed={() => setDashboardPush(null)}
            />
          }
        />
        <Route path="*" element={<Navigate to="/queries" replace />} />
      </Routes>
    </main>
  )
}

function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}

export default App
