import { serveDir, serveFile } from "jsr:@std/http@^1.0.0/file-server"
import { fromFileUrl, join } from "jsr:@std/path@^1.0.0"

type Item = { id: number; name: string }

const items: Item[] = [
  { id: 1, name: "Welcome to QueryView" },
  { id: 2, name: "Edit backend/main.ts to extend the API" },
  { id: 3, name: "Run e2e tests with `deno task test:e2e`" },
]
let nextId = items.length + 1

const STATIC_ROOT = Deno.env.get("STATIC_ROOT") ??
  fromFileUrl(new URL("../frontend/dist", import.meta.url))
const SERVE_STATIC = Deno.env.get("SERVE_STATIC") === "1"

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers ?? {}),
    },
  })
}

async function handleApi(req: Request, pathname: string): Promise<Response | null> {
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

  if (pathname.startsWith("/api/")) {
    return json({ error: "not found" }, { status: 404 })
  }

  return null
}

async function handler(req: Request): Promise<Response> {
  const { pathname } = new URL(req.url)

  const apiResponse = await handleApi(req, pathname)
  if (apiResponse) return apiResponse

  if (!SERVE_STATIC) {
    return json({ error: "not found" }, { status: 404 })
  }

  const fileResponse = await serveDir(req, { fsRoot: STATIC_ROOT, quiet: true })
  if (fileResponse.status !== 404) return fileResponse

  // SPA fallback: serve index.html for any unknown path so client-side
  // routing works. The browser still gets a 200 with the SPA shell.
  return serveFile(req, join(STATIC_ROOT, "index.html"))
}

const port = Number(Deno.env.get("PORT") ?? 8000)
Deno.serve({ port }, handler)
