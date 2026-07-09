// Client for the /api/git backup/restore endpoints (see docs/gitsync.md).

export type GitKind = 'query' | 'dashboard'

export type GitRevision = { sha: string; date: number; message: string }

export type GitStoreResult = {
  ok: boolean
  committed?: boolean
  sha?: string | null
  message?: string
}

export type GitHistoryResult = {
  ok: boolean
  revisions?: GitRevision[]
  has_more?: boolean
  message?: string
}

// Whether git sync is configured is server env config — fetch it once and
// share the promise across every GitSyncControls instance.
let statusCache: Promise<boolean> | undefined

export function gitStatus(): Promise<boolean> {
  statusCache ??= (async () => {
    try {
      const r = await (await fetch('/api/git/status')).json()
      return Boolean(r.configured)
    } catch {
      return false
    }
  })()
  return statusCache
}

export async function gitStore(
  kind: GitKind,
  name: string,
  connType?: string,
): Promise<GitStoreResult> {
  const res = await fetch('/api/git/store', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, name, conn_type: connType }),
  })
  return res.json()
}

export async function gitHistory(
  kind: GitKind,
  name: string,
  opts: { connType?: string; before?: string; limit?: number } = {},
): Promise<GitHistoryResult> {
  const params = new URLSearchParams({ kind, name })
  if (opts.connType) params.set('conn_type', opts.connType)
  if (opts.before) params.set('before', opts.before)
  if (opts.limit) params.set('limit', String(opts.limit))
  const res = await fetch(`/api/git/history?${params.toString()}`)
  return res.json()
}

export async function gitRestore(
  kind: GitKind,
  name: string,
  ref: string,
  connType?: string,
): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch('/api/git/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, name, conn_type: connType, ref }),
  })
  return res.json()
}

// Merge a fetched page into the already-loaded revisions, dropping any shas
// we already have (pages can overlap if a commit lands mid-scroll).
export function appendRevisions(
  existing: GitRevision[],
  page: GitRevision[],
): GitRevision[] {
  const seen = new Set(existing.map((r) => r.sha))
  return [...existing, ...page.filter((r) => !seen.has(r.sha))]
}
