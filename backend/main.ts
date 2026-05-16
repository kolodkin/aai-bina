type Item = { id: number; name: string }

const items: Item[] = [
  { id: 1, name: "Welcome to QueryView" },
  { id: 2, name: "Edit backend/main.ts to extend the API" },
  { id: 3, name: "Run e2e tests with `deno task test:e2e`" },
]
let nextId = items.length + 1

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers ?? {}),
    },
  })
}

async function handler(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const { pathname } = url

  if (req.method === "GET" && pathname === "/api/health") {
    return json({ status: "ok", service: "queryview-backend" })
  }

  if (req.method === "GET" && pathname === "/api/items") {
    return json(items)
  }

  if (req.method === "POST" && pathname === "/api/items") {
    let body: unknown
    try {
      body = await req.json()
    } catch {
      return json({ error: "invalid JSON" }, { status: 400 })
    }
    const name = (body as { name?: unknown })?.name
    if (typeof name !== "string" || name.trim() === "") {
      return json({ error: "name required" }, { status: 400 })
    }
    const item: Item = { id: nextId++, name: name.trim() }
    items.push(item)
    return json(item, { status: 201 })
  }

  return json({ error: "not found" }, { status: 404 })
}

const port = Number(Deno.env.get("PORT") ?? 8000)
Deno.serve({ port }, handler)
