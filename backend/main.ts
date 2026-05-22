import { serveDir, serveFile } from "jsr:@std/http@^1.0.0/file-server"
import { fromFileUrl, join } from "jsr:@std/path@^1.0.0"
import { parseChConfig, testConnection } from "./clickhouse.ts"
import { connectNew, getSession, openSaved, selectDatabase } from "./connect.ts"

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

async function readJson(req: Request): Promise<unknown | undefined> {
  try {
    return await req.json()
  } catch {
    return undefined
  }
}

async function handleApi(
  req: Request,
  pathname: string,
  sid: string,
): Promise<Response | null> {
  if (req.method === "GET" && pathname === "/api/health") {
    return json({ status: "ok", service: "queryview-backend" })
  }

  if (req.method === "GET" && pathname === "/api/session") {
    return json(await getSession(sid))
  }

  // Test only: a throwaway connectivity check, no save, no activation.
  if (req.method === "POST" && pathname === "/api/clickhouse/test") {
    const parsed = parseChConfig(await readJson(req))
    if ("error" in parsed) {
      return json({ ok: false, message: parsed.error }, { status: 400 })
    }
    return json(await testConnection(parsed.config))
  }

  // Create + open a connection for this session.
  if (req.method === "POST" && pathname === "/api/clickhouse/connect") {
    const body = await readJson(req)
    const parsed = parseChConfig(body)
    if ("error" in parsed) {
      return json({ ok: false, message: parsed.error }, { status: 400 })
    }
    const b = (body ?? {}) as Record<string, unknown>
    const name = typeof b.name === "string" && b.name.trim()
      ? b.name.trim()
      : "clickhouse"
    return json(await connectNew(sid, name, parsed.config))
  }

  // Open a saved connection by name for this session (connect <name>).
  if (req.method === "POST" && pathname === "/api/clickhouse/open") {
    const b = (await readJson(req) ?? {}) as Record<string, unknown>
    const name = typeof b.name === "string" ? b.name.trim() : ""
    if (!name) return json({ ok: false, message: "name required" }, { status: 400 })
    const r = await openSaved(sid, name)
    if (!r.ok) {
      return json({ ok: false, message: r.message }, {
        status: r.notFound ? 404 : 200,
      })
    }
    return json({ ok: true, name: r.name, databases: r.databases })
  }

  // Select this session's active connection's database.
  if (req.method === "POST" && pathname === "/api/clickhouse/database") {
    const b = (await readJson(req) ?? {}) as Record<string, unknown>
    const database = typeof b.database === "string" ? b.database : ""
    const r = selectDatabase(sid, database)
    if (!r.ok) {
      return json({ ok: false, message: r.message }, {
        status: r.reason === "no-session" ? 409 : 400,
      })
    }
    return json({ ok: true })
  }

  if (pathname.startsWith("/api/")) {
    return json({ error: "not found" }, { status: 404 })
  }

  return null
}

function cookieValue(header: string | null, name: string): string | undefined {
  if (!header) return undefined
  for (const part of header.split(";")) {
    const eq = part.indexOf("=")
    if (eq === -1) continue
    if (part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim()
  }
  return undefined
}

async function route(
  req: Request,
  pathname: string,
  sid: string,
): Promise<Response> {
  const apiResponse = await handleApi(req, pathname, sid)
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

async function handler(req: Request): Promise<Response> {
  const { pathname } = new URL(req.url)

  let sid = cookieValue(req.headers.get("cookie"), "qv_session")
  const newSession = !sid
  if (!sid) sid = crypto.randomUUID()

  const res = await route(req, pathname, sid)
  if (newSession) {
    res.headers.append(
      "set-cookie",
      `qv_session=${sid}; Path=/; HttpOnly; SameSite=Lax`,
    )
  }
  return res
}

const port = Number(Deno.env.get("PORT") ?? 8000)
Deno.serve({ port }, handler)
