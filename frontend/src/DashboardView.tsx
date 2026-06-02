import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

export type DashboardPush = {
  name: string
  connection: string
  html: string
  queries: Record<string, string>
}

type DashboardSummary = { name: string; connection: string; updated_at: number }

// Column-oriented results map: {query_name: {column_name: values[]}}.
type Results = Record<string, Record<string, unknown[]>>

// Build the iframe document: a prologue that exposes the results as
// `window.queries`, then the agent-authored HTML. The JSON has `<` escaped so an
// embedded `</script>` in result data can't break out of the prologue script.
function buildSrcDoc(html: string, results: Results): string {
  const safeJson = JSON.stringify(results).replace(/</g, '\\u003c')
  return `<script>window.queries = ${safeJson};</script>\n${html}`
}

// The dashboard page (`/dashboard?name=x`). Picks a saved dashboard (dropdown or
// `?name=`), runs its queries against the named connection via /api/runqueries,
// and renders the agent HTML in a sandboxed iframe with the results injected as
// `window.queries`. A freshly-pushed dashboard renders without a refetch.
function DashboardView({
  pushed,
  onPushConsumed,
}: {
  pushed?: DashboardPush | null
  onPushConsumed?: () => void
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const name = searchParams.get('name') ?? ''

  const [dashboards, setDashboards] = useState<DashboardSummary[]>([])
  // A pushed dashboard is captured locally so consuming it from the shell
  // doesn't re-trigger the resolve effect below.
  const [localPush, setLocalPush] = useState<DashboardPush | null>(null)
  const [active, setActive] = useState<DashboardPush | null>(null)
  const [results, setResults] = useState<Results | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Load the list for the dropdown (and refresh it whenever a push lands, so a
  // newly-created dashboard appears).
  useEffect(() => {
    let cancelled = false
    fetch('/api/dashboards')
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setDashboards((d.dashboards ?? []) as DashboardSummary[])
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [pushed])

  // Capture a pushed dashboard and release it from the shell.
  useEffect(() => {
    if (pushed) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLocalPush(pushed)
      onPushConsumed?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushed])

  // Resolve the selected dashboard (pushed payload if it matches, else the
  // store), then run its queries. Fail-fast: a non-2xx /api/runqueries response
  // surfaces as a dashboard-level error and no iframe is rendered.
  useEffect(() => {
    let cancelled = false

    // The selected dashboard: the pushed payload if it matches, else the store.
    // Returns null (and sets an error) if it can't be loaded.
    async function loadDashboard(): Promise<DashboardPush | null> {
      if (localPush && localPush.name === name) return localPush
      try {
        const res = await fetch(`/api/dashboards/${encodeURIComponent(name)}`)
        if (!res.ok) {
          if (!cancelled) setError(`Dashboard “${name}” not found.`)
          return null
        }
        return (await res.json()) as DashboardPush
      } catch {
        if (!cancelled) setError('Failed to load dashboard.')
        return null
      }
    }

    async function resolve() {
      setError(null)
      setResults(null)
      setActive(null)
      if (!name) return

      const dash = await loadDashboard()
      if (cancelled || !dash) return
      setActive(dash)

      setLoading(true)
      try {
        const res = await fetch('/api/runqueries', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connection: dash.connection, queries: dash.queries }),
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok || !data.ok) {
          if (!cancelled) setError(data.message ?? 'Failed to run queries.')
          return
        }
        if (!cancelled) setResults((data.results ?? {}) as Results)
      } catch {
        if (!cancelled) setError('Failed to run queries.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void resolve()
    return () => {
      cancelled = true
    }
  }, [name, localPush])

  const srcDoc = useMemo(
    () => (active && results ? buildSrcDoc(active.html, results) : null),
    [active, results],
  )

  return (
    <div className="w-full max-w-[80vw]" data-testid="dashboard-view">
      <div className="mb-4 flex items-center justify-center gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-white [text-shadow:0_2px_30px_rgba(129,140,248,0.45)]">
          Dashboard
        </h1>
        <select
          data-testid="dashboard-select"
          aria-label="Dashboards"
          value={name}
          onChange={(e) => {
            const next = e.target.value
            if (next) setSearchParams({ name: next })
            else setSearchParams({})
          }}
          className="glass-input min-w-48 px-3 py-2 text-sm"
        >
          <option value="">Select a dashboard…</option>
          {name !== '' && !dashboards.some((d) => d.name === name) && (
            <option value={name}>{name}</option>
          )}
          {dashboards.map((d) => (
            <option key={d.name} value={d.name}>
              {d.name}
            </option>
          ))}
        </select>
      </div>

      {!name && (
        <p className="text-center text-sm text-slate-400" data-testid="dashboard-empty">
          {dashboards.length
            ? 'Pick a dashboard to view it.'
            : 'No dashboards yet. An agent can create one with the upsert_dashboard tool.'}
        </p>
      )}

      {error && (
        <p
          className="rounded-xl border border-red-400/40 bg-red-500/10 px-4 py-3 text-center text-sm text-red-200"
          data-testid="dashboard-error"
        >
          {error}
        </p>
      )}

      {name && !error && loading && !srcDoc && (
        <p className="text-center text-sm text-slate-400" data-testid="dashboard-loading">
          Running queries…
        </p>
      )}

      {srcDoc && (
        <iframe
          title="dashboard"
          data-testid="dashboard-frame"
          sandbox="allow-scripts"
          srcDoc={srcDoc}
          className="h-[78vh] w-full rounded-xl border border-white/10 bg-white"
        />
      )}
    </div>
  )
}

export default DashboardView
