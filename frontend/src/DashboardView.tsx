import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  answerBridgeRequest,
  buildSrcDoc,
  parseBridgeRequest,
  parseParamsRequest,
  type ResolvedParam,
  type Results,
} from './dashboardBridge'
import {
  parseDashboardParams,
  resolveOrder,
  substituteParams,
  type DashboardParam,
} from './dashboardParams'
import GitSyncControls from './GitSyncControls'
import { activeWorkspace } from './workspace'

export type DashboardPush = {
  name: string
  connection: string
  html: string
  queries: Record<string, string>
  params?: unknown[]
}

type DashboardSummary = { name: string; connection: string; updated_at: number }

// Run a dashboard's queries against its connection; shared by the initial load
// and the iframe's on-demand runQueries bridge.
async function runQueries(connection: string, queries: Record<string, string>) {
  const res = await fetch('/api/runqueries', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ connection, queries }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok || !data.ok) {
    return { ok: false as const, message: (data.message as string) ?? 'Failed to run queries.' }
  }
  return { ok: true as const, results: (data.results ?? {}) as Results }
}

type Runner = (queries: Record<string, string>) => ReturnType<typeof runQueries>

// Substitute the resolved selections into every named query of the dashboard.
function applyToQueries(
  queries: Record<string, string>,
  declared: DashboardParam[],
  resolved: ResolvedParam[],
): Record<string, string> {
  const values = Object.fromEntries(resolved.map((p) => [p.name, p.value]))
  return Object.fromEntries(
    Object.entries(queries).map(([name, sql]) => [name, substituteParams(sql, declared, values)]),
  )
}

// Resolve each selector's option list in dependency order — an options_sql may
// reference an already-resolved param — keeping the caller's values where they
// are still valid and otherwise falling back to the first option.
async function resolveParams(
  params: DashboardParam[],
  values: Record<string, string>,
  run: Runner,
): Promise<ResolvedParam[]> {
  const resolved: ResolvedParam[] = []
  const chosen: Record<string, string> = {}

  for (const p of resolveOrder(params)) {
    let options = p.options ?? []
    if (p.optionsSql) {
      const sql = substituteParams(p.optionsSql, params, chosen)
      const r = await run({ o: sql })
      if (!r.ok) throw new Error(r.message)
      const table = r.results.o ?? {}
      const firstColumn = Object.values(table)[0] ?? []
      options = firstColumn.map(String)
    }
    // A dimension is a checkbox: its value is the toggle, not a choice.
    const want = values[p.name]
    const value =
      p.kind === 'dimension'
        ? (want ?? '')
        : want && options.includes(want)
          ? want
          : (options[0] ?? '')
    chosen[p.name] = value
    resolved.push({ name: p.name, kind: p.kind, options, value })
  }
  return resolved
}

// The dashboard page (`/dashboard?name=x`). Picks a saved dashboard (dropdown or
// `?name=`), runs its queries via /api/runqueries, and renders the agent HTML in
// a sandboxed iframe with results injected as `window.queries`. A pushed
// dashboard renders without a refetch.
function DashboardView({
  pushed,
  onPushConsumed,
  database,
}: {
  pushed?: DashboardPush | null
  onPushConsumed?: () => void
  // Active connection database; a change re-runs the dashboard's queries.
  database?: string | null
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const name = searchParams.get('name') ?? ''

  const [dashboards, setDashboards] = useState<DashboardSummary[]>([])
  // Captured locally so consuming the shell push doesn't re-trigger resolve.
  const [localPush, setLocalPush] = useState<DashboardPush | null>(null)
  const [active, setActive] = useState<DashboardPush | null>(null)
  const [results, setResults] = useState<Results | null>(null)
  const [params, setParams] = useState<ResolvedParam[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  // Bumped after a git restore to re-trigger the load effect below without
  // otherwise changing its dependencies.
  const [reloadNonce, setReloadNonce] = useState(0)

  // Refetch the dropdown list (also used after a Save to surface a new name).
  async function loadDashboards() {
    try {
      const d = await (await fetch(`/api/dashboards?workspace=${encodeURIComponent(activeWorkspace())}`)).json()
      setDashboards((d.dashboards ?? []) as DashboardSummary[])
    } catch {
      /* non-fatal; keep the last list */
    }
  }

  // User-only persist: an agent push renders a draft; this Save writes the
  // currently-active dashboard (draft or loaded) to the store.
  async function save() {
    if (!active) return
    setSaving(true)
    setError(null)
    try {
      const res = await fetch('/api/dashboards', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: active.name,
          connection: active.connection,
          html: active.html,
          queries: active.queries,
          // Declarations, not the resolved values — a saved dashboard re-resolves
          // its selectors on every load.
          params: active.params ?? [],
          workspace: activeWorkspace(),
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok || !data.ok) {
        setError(data.message ?? 'Failed to save dashboard.')
        return
      }
      await loadDashboards()
    } catch {
      setError('Failed to save dashboard.')
    } finally {
      setSaving(false)
    }
  }

  // Load the dropdown list; refresh on each push so a new dashboard appears.
  useEffect(() => {
    let cancelled = false
    fetch(`/api/dashboards?workspace=${encodeURIComponent(activeWorkspace())}`)
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

  // Resolve the selected dashboard (pushed payload if it matches, else the store),
  // then run its queries. Fail-fast: a non-2xx /api/runqueries response surfaces
  // as a dashboard-level error and renders no iframe.
  useEffect(() => {
    let cancelled = false

    // The selected dashboard; returns null (and sets an error) if it can't load.
    async function loadDashboard(): Promise<DashboardPush | null> {
      if (localPush && localPush.name === name) return localPush
      try {
        const res = await fetch(
          `/api/dashboards/${encodeURIComponent(name)}?workspace=${encodeURIComponent(activeWorkspace())}`,
        )
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
        const declared = parseDashboardParams(dash.params)
        const run: Runner = (queries) => runQueries(dash.connection, queries)
        const resolved = await resolveParams(declared, {}, run)
        if (cancelled) return
        setParams(resolved)

        const r = await run(applyToQueries(dash.queries, declared, resolved))
        if (cancelled) return
        if (!r.ok) {
          setError(r.message)
          return
        }
        setResults(r.results)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to run queries.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void resolve()
    return () => {
      cancelled = true
    }
  }, [name, localPush, database, reloadNonce])

  // Built once per load; later param changes are answered over the bridge so the
  // iframe keeps its state instead of reloading.
  const srcDoc = useMemo(
    () => (active && results ? buildSrcDoc(active.html, results, params) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [active, results],
  )

  // Serve the iframe's bridge. Only messages from this dashboard's own frame are
  // answered, and always against the dashboard's connection. A set-params
  // message carries values only: the SQL comes from the dashboard's own queries
  // and is substituted here, so the page never issues SQL of its own.
  const frameRef = useRef<HTMLIFrameElement>(null)
  const connection = active?.connection
  const declared = useMemo(() => parseDashboardParams(active?.params), [active])
  useEffect(() => {
    if (!connection || !active) return
    const run: Runner = (queries) => runQueries(connection, queries)

    async function onMessage(e: MessageEvent) {
      const frame = frameRef.current
      if (!frame || e.source !== frame.contentWindow) return

      const values = parseParamsRequest(e.data)
      if (values) {
        try {
          const resolved = await resolveParams(declared, values, run)
          const r = await run(applyToQueries(active!.queries, declared, resolved))
          frame.contentWindow?.postMessage(
            r.ok
              ? { type: 'params-results', ok: true, results: r.results, params: resolved }
              : { type: 'params-results', ok: false, message: r.message, params: resolved },
            '*',
          )
        } catch (err) {
          frame.contentWindow?.postMessage(
            {
              type: 'params-results',
              ok: false,
              message: err instanceof Error ? err.message : 'Failed to run queries.',
              params: [],
            },
            '*',
          )
        }
        return
      }

      const req = parseBridgeRequest(e.data)
      if (!req) return
      frame.contentWindow?.postMessage(await answerBridgeRequest(req, run), '*')
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [connection, active, declared])

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
        <button
          type="button"
          data-testid="dashboard-save"
          onClick={() => void save()}
          disabled={!active || saving}
          className="glass-btn min-w-[5rem] px-3 py-2 text-center text-sm font-medium"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <GitSyncControls
          kind="dashboard"
          name={name}
          disabled={!name}
          onRestored={() => {
            setLocalPush(null)
            setReloadNonce((n) => n + 1)
          }}
        />
      </div>

      {!name && (
        <p className="text-center text-sm text-slate-400" data-testid="dashboard-empty">
          {dashboards.length
            ? 'Pick a dashboard to view it.'
            : 'No dashboards yet. An agent can create one with the push_dashboard tool.'}
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
          ref={frameRef}
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
