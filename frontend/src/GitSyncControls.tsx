import { useCallback, useEffect, useState, type UIEvent } from 'react'
import {
  appendRevisions,
  gitHistory,
  gitRestore,
  gitStatus,
  gitStore,
  type GitKind,
  type GitRevision,
} from './gitsync'

type Props = {
  kind: GitKind
  name: string
  connType?: string
  disabled?: boolean
  // Called after a successful restore so the host view reloads the entity.
  onRestored: () => void
}

// Commit / Restore pair for one entity. Commit pushes the *saved* DB state to
// the configured git remote; Restore opens that entity's revision list (10 at
// a time, infinite scroll) and overwrites the local copy with a picked one.
export default function GitSyncControls({ kind, name, connType, disabled, onRestored }: Props) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [committing, setCommitting] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [revisions, setRevisions] = useState<GitRevision[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    void gitStatus().then(setConfigured)
  }, [])

  // The picker's rows belong to one entity — switching entities while it is
  // open must not leave another entity's revisions clickable.
  useEffect(() => {
    setOpen(false)
    setRevisions([])
    setHasMore(false)
    setError('')
  }, [kind, name, connType])

  const off = disabled || !name.trim() || configured !== true
  const tooltip =
    configured === false ? 'Git sync is not configured (set GIT_SYNC_REMOTE)' : undefined

  function flash(text: string) {
    setNote(text)
    window.setTimeout(() => setNote(''), 2500)
  }

  async function commit() {
    setCommitting(true)
    setError('')
    try {
      const r = await gitStore(kind, name, connType)
      if (r.ok) flash(r.committed ? 'Committed' : 'No changes')
      else setError(r.message ?? 'commit failed')
    } finally {
      setCommitting(false)
    }
  }

  const loadPage = useCallback(
    async (before?: string) => {
      setLoading(true)
      setError('')
      try {
        const r = await gitHistory(kind, name, { connType, before, limit: 10 })
        if (!r.ok) {
          setError(r.message ?? 'history failed')
          return
        }
        setRevisions((prev) => (before ? appendRevisions(prev, r.revisions ?? []) : (r.revisions ?? [])))
        setHasMore(Boolean(r.has_more))
      } finally {
        setLoading(false)
      }
    },
    [kind, name, connType],
  )

  async function toggleOpen() {
    const next = !open
    setOpen(next)
    if (next) {
      setRevisions([])
      setHasMore(false)
      void loadPage()
    }
  }

  function onScroll(e: UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40
    if (nearBottom && hasMore && !loading && revisions.length > 0) {
      void loadPage(revisions[revisions.length - 1].sha)
    }
  }

  async function restore(sha: string) {
    if (!window.confirm(`Overwrite local '${name}' with this version?`)) return
    setError('')
    const r = await gitRestore(kind, name, sha, connType)
    if (!r.ok) {
      setError(r.message ?? 'restore failed')
      return
    }
    setOpen(false)
    onRestored()
  }

  return (
    <div className="relative flex items-center gap-2">
      <button
        type="button"
        data-testid="git-commit"
        onClick={() => void commit()}
        disabled={off || committing}
        title={tooltip}
        className="glass-btn px-3 py-2 font-medium"
      >
        {committing ? 'Committing…' : note || 'Commit'}
      </button>
      <button
        type="button"
        data-testid="git-restore-toggle"
        onClick={() => void toggleOpen()}
        disabled={off}
        title={tooltip}
        className="glass-btn px-3 py-2 font-medium"
      >
        Restore
      </button>
      {open && (
        <div
          data-testid="git-revisions"
          onScroll={onScroll}
          className="glass-panel absolute right-0 top-full z-10 mt-2 max-h-72 w-96 overflow-y-auto p-2"
        >
          {revisions.map((r) => (
            <div
              key={r.sha}
              data-testid="git-revision-row"
              className="flex items-center gap-2 border-b border-white/10 px-2 py-1.5 text-sm last:border-b-0"
            >
              <code className="shrink-0 text-xs text-slate-400">{r.sha.slice(0, 7)}</code>
              <span className="shrink-0 text-xs text-slate-400">
                {new Date(r.date).toLocaleString()}
              </span>
              <span className="min-w-0 flex-1 truncate" title={r.message}>
                {r.message}
              </span>
              <button
                type="button"
                data-testid="git-revision-restore"
                onClick={() => void restore(r.sha)}
                className="glass-btn shrink-0 px-2 py-1 text-xs font-medium"
              >
                Restore
              </button>
            </div>
          ))}
          {loading && <p className="px-2 py-1.5 text-xs text-slate-400">Loading…</p>}
          {!loading && revisions.length === 0 && (
            <p className="px-2 py-1.5 text-xs text-slate-400">No commits yet.</p>
          )}
        </div>
      )}
      {error && (
        <p data-testid="git-error" className="text-xs text-red-300">
          {error}
        </p>
      )}
    </div>
  )
}
