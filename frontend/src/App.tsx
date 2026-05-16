import { useEffect, useState } from 'react'

type Item = { id: number; name: string }

type HealthResponse = { status: string; service: string }

function App() {
  const [items, setItems] = useState<Item[] | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState('')

  async function loadItems() {
    try {
      const res = await fetch('/api/items')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setItems(await res.json())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'unknown error')
    }
  }

  useEffect(() => {
    loadItems()
    fetch('/api/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  async function addItem(e: React.FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    const res = await fetch('/api/items', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName.trim() }),
    })
    if (res.ok) {
      setNewName('')
      await loadItems()
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-2xl px-6 py-12">
        <header className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight">QueryView</h1>
          <p className="mt-2 text-slate-600">
            Deno + Vite + React + Tailwind starter
          </p>
          {health && (
            <p
              className="mt-2 text-sm text-emerald-700"
              data-testid="health-status"
            >
              {health.service}: {health.status}
            </p>
          )}
        </header>

        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-xl font-semibold">Items</h2>

          <form onSubmit={addItem} className="mb-6 flex gap-2">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New item name"
              aria-label="New item name"
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200"
            />
            <button
              type="submit"
              className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
              disabled={!newName.trim()}
            >
              Add
            </button>
          </form>

          {error && (
            <p className="mb-4 text-sm text-red-600" data-testid="error">
              Failed to load items: {error}
            </p>
          )}

          {items === null && !error && (
            <p className="text-slate-500">Loading…</p>
          )}

          {items && items.length === 0 && (
            <p className="text-slate-500">No items yet.</p>
          )}

          {items && items.length > 0 && (
            <ul className="divide-y divide-slate-200" data-testid="item-list">
              {items.map((item) => (
                <li
                  key={item.id}
                  className="flex items-center justify-between py-3"
                >
                  <span>{item.name}</span>
                  <span className="text-xs text-slate-400">#{item.id}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  )
}

export default App
