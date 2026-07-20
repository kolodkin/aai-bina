import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import yaml from 'js-yaml'

import {
  answerBridgeRequest,
  buildSrcDoc,
  parseBridgeRequest,
  parseParamsRequest,
  type Results,
} from './dashboardBridge'
import {
  missingParams,
  parseDashboardParams,
  resolveParams,
  substituteParams,
  type DashboardParam,
  type ResolvedParam,
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

// Substitute the resolved selections into the dashboard's queries. A query
// whose selectors aren't all chosen yet is left out rather than run half-blank:
// with `default: none` the dashboard opens with nothing selected and waits.
function applyToQueries(
  queries: Record<string, string>,
  declared: DashboardParam[],
  resolved: ResolvedParam[],
): Record<string, string> {
  const values = Object.fromEntries(resolved.map((p) => [p.name, p.value]))
  return Object.fromEntries(
    Object.entries(queries)
      .filter(([, sql]) => missingParams(sql, declared, values).length === 0)
      .map(([name, sql]) => [name, substituteParams(sql, declared, values)]),
  )
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
  // Whether the active dashboard has unsaved changes. Only an agent push makes
  // it diverge from the store (the page never edits html/queries in place), so
  // a store-loaded dashboard is clean and Save is disabled ("nothing to save").
  const [dirty, setDirty] = useState(false)
  const [showQueries, setShowQueries] = useState(false)

  // The active dashboard's queries as YAML (name → SQL), matching the repo's
  // queries.yaml. js-yaml renders multiline SQL as readable literal blocks.
  const queriesYaml = useMemo(
    () => (active ? yaml.dump(active.queries ?? {}, { lineWidth: -1 }) : ''),
    [active],
  )
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
      setDirty(false)
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
      // Dirty only when the active dashboard is a pending agent push (a draft
      // not yet in the store); a store-loaded one matches the DB.
      setDirty(!!(localPush && localPush.name === name))

      setLoading(true)
      try {
        const declared = parseDashboardParams(dash.params)
        const run: Runner = (queries) => runQueries(dash.connection, queries)
        const resolved = await resolveParams(declared, {}, run)
        if (cancelled) return
        setParams(resolved)

        const runnable = applyToQueries(dash.queries, declared, resolved)
        if (!Object.keys(runnable).length) {
          setResults({})
          return
        }
        const r = await run(runnable)
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
          const runnable = applyToQueries(active!.queries, declared, resolved)
          const r = Object.keys(runnable).length
            ? await run(runnable)
            : { ok: true as const, results: {} as Results }
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
          disabled={!active || saving || !dirty}
          title={!dirty && active ? 'No unsaved changes' : undefined}
          className="glass-btn min-w-[5rem] px-3 py-2 text-center text-sm font-medium"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          data-testid="dashboard-queries-yaml"
          onClick={() => setShowQueries(true)}
          disabled={!active || Object.keys(active.queries ?? {}).length === 0}
          title="View the dashboard's queries as YAML"
          className="glass-btn px-3 py-2 text-sm font-medium"
        >
          Queries
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

      {showQueries && active && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Dashboard queries (YAML)"
          data-testid="dashboard-queries-modal"
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/50 p-6 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget) setShowQueries(false)
          }}
        >
          <div className="glass-popover flex max-h-[80vh] w-full max-w-2xl flex-col p-5">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h3 className="text-base font-semibold text-slate-100">Queries (YAML)</h3>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="dashboard-queries-copy"
                  onClick={() => void navigator.clipboard?.writeText(queriesYaml)}
                  className="glass-btn px-2 py-1 text-xs font-medium text-indigo-200"
                >
                  Copy
                </button>
                <button
                  type="button"
                  data-testid="dashboard-queries-close"
                  onClick={() => setShowQueries(false)}
                  className="glass-btn px-2 py-1 text-xs"
                >
                  Close
                </button>
              </div>
            </div>
            <pre
              data-testid="dashboard-queries-yaml-text"
              className="min-h-0 flex-1 overflow-auto rounded bg-black/30 p-3 font-mono text-xs text-slate-100"
            >
              {queriesYaml}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

export default DashboardView
