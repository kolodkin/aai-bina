import { serveDir, serveFile } from "jsr:@std/http@^1.0.0/file-server"
import { fromFileUrl, join } from "jsr:@std/path@^1.0.0"
import { decodeBase64, encodeBase64 } from "jsr:@std/encoding@^1/base64"
import { DatabaseSync } from "node:sqlite"
import {
  type ChConfig,
  listDatabases,
  parseChConfig,
  testConnection,
} from "./clickhouse.ts"

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

// --- Storage (SQLite) -----------------------------------------------------
const DB_PATH = Deno.env.get("DB_PATH") ??
  fromFileUrl(new URL("./queryview.db", import.meta.url))
const db = new DatabaseSync(DB_PATH)
db.exec(`
  CREATE TABLE IF NOT EXISTS connections (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    host           TEXT NOT NULL,
    port           INTEGER NOT NULL,
    username       TEXT NOT NULL,
    password       TEXT NOT NULL,
    database       TEXT,
    last_active_at INTEGER NOT NULL
  )
`)

// --- Password encryption at rest (AES-256-GCM) ----------------------------
// The key comes from DB_ENCRYPTION_KEY (base64, 32 bytes) or a generated local
// key file next to the DB (gitignored). Stored values are base64(iv ‖ ciphertext).
const KEY_PATH = Deno.env.get("DB_KEY_PATH") ?? `${DB_PATH}.key`

async function loadOrCreateKey(): Promise<CryptoKey> {
  let raw: Uint8Array
  const envKey = Deno.env.get("DB_ENCRYPTION_KEY")
  if (envKey) {
    raw = decodeBase64(envKey)
  } else {
    try {
      raw = await Deno.readFile(KEY_PATH)
    } catch {
      raw = crypto.getRandomValues(new Uint8Array(32))
      await Deno.writeFile(KEY_PATH, raw, { mode: 0o600 })
    }
  }
  return await crypto.subtle.importKey("raw", new Uint8Array(raw), "AES-GCM", false, [
    "encrypt",
    "decrypt",
  ])
}
const encryptionKey = await loadOrCreateKey()

async function encryptPassword(plain: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      encryptionKey,
      new Uint8Array(new TextEncoder().encode(plain)),
    ),
  )
  const combined = new Uint8Array(iv.length + ct.length)
  combined.set(iv, 0)
  combined.set(ct, iv.length)
  return encodeBase64(combined)
}

async function decryptPassword(stored: string): Promise<string> {
  const combined = new Uint8Array(decodeBase64(stored))
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: combined.subarray(0, 12) },
    encryptionKey,
    combined.subarray(12),
  )
  return new TextDecoder().decode(pt)
}

type StoredConnection = ChConfig & { name: string; database: string | null }

async function saveActiveConnection(name: string, c: ChConfig): Promise<void> {
  const password = await encryptPassword(c.password)
  db.prepare(
    `INSERT INTO connections (name, host, port, username, password, last_active_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(name) DO UPDATE SET
       host = excluded.host, port = excluded.port,
       username = excluded.username, password = excluded.password,
       last_active_at = excluded.last_active_at`,
  ).run(name, c.host, c.port, c.username, password, Date.now())
}

function saveSelectedDatabase(name: string, database: string): void {
  db.prepare(`UPDATE connections SET database = ? WHERE name = ?`)
    .run(database, name)
}

async function rowToConnection(
  row: Record<string, unknown> | undefined,
): Promise<StoredConnection | null> {
  if (!row) return null
  let password: string
  try {
    password = await decryptPassword(String(row.password))
  } catch {
    // Unreadable (key changed / legacy plaintext) — treat as unavailable.
    return null
  }
  return {
    name: String(row.name),
    host: String(row.host),
    port: Number(row.port),
    username: String(row.username),
    password,
    database: row.database == null ? null : String(row.database),
  }
}

function latestActiveConnection(): Promise<StoredConnection | null> {
  return rowToConnection(
    db.prepare(
      `SELECT name, host, port, username, password, database
       FROM connections ORDER BY last_active_at DESC LIMIT 1`,
    ).get() as Record<string, unknown> | undefined,
  )
}

function connectionByName(name: string): Promise<StoredConnection | null> {
  return rowToConnection(
    db.prepare(
      `SELECT name, host, port, username, password, database
       FROM connections WHERE name = ?`,
    ).get(name) as Record<string, unknown> | undefined,
  )
}

function touchConnection(name: string): void {
  db.prepare(`UPDATE connections SET last_active_at = ? WHERE name = ?`)
    .run(Date.now(), name)
}

// --- Sessions (one active connection per session, keyed by a cookie) ------
type Session = {
  name: string
  config: ChConfig
  databases: string[]
  database: string | null
}
const sessions = new Map<string, Session>()

// Open a steady connection: list its databases and build a session object.
async function buildSession(
  name: string,
  config: ChConfig,
  database: string | null,
): Promise<{ ok: true; session: Session } | { ok: false; message: string }> {
  const r = await listDatabases(config)
  if (!r.ok) return { ok: false, message: r.message }
  const databases = r.databases
  return {
    ok: true,
    session: {
      name,
      config,
      databases,
      database: database && databases.includes(database) ? database : null,
    },
  }
}

// At session start (a cookie we haven't seen), reconnect the latest active
// connection so a fresh session resumes where the last one left off.
async function ensureSession(sid: string): Promise<void> {
  if (sessions.has(sid)) return
  const stored = await latestActiveConnection()
  if (!stored) return
  const { name, database, ...config } = stored
  const built = await buildSession(name, config, database)
  if (built.ok) sessions.set(sid, built.session)
}

function sessionState(s: Session | undefined): Record<string, unknown> {
  return s
    ? {
      connected: true,
      name: s.name,
      databases: s.databases,
      database: s.database,
    }
    : { connected: false }
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
    await ensureSession(sid)
    return json(sessionState(sessions.get(sid)))
  }

  // Test only: a throwaway connectivity check, no save, no activation.
  if (req.method === "POST" && pathname === "/api/clickhouse/test") {
    const parsed = parseChConfig(await readJson(req))
    if ("error" in parsed) {
      return json({ ok: false, message: parsed.error }, { status: 400 })
    }
    return json(await testConnection(parsed.config))
  }

  // Open a steady connection: list databases, persist, and activate it for
  // this session.
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
    const built = await buildSession(name, parsed.config, null)
    if (!built.ok) return json({ ok: false, message: built.message })
    sessions.set(sid, built.session)
    await saveActiveConnection(name, parsed.config)
    return json({ ok: true, name, databases: built.session.databases })
  }

  // Open a saved connection by name for this session (connect <name>).
  if (req.method === "POST" && pathname === "/api/clickhouse/open") {
    const b = (await readJson(req) ?? {}) as Record<string, unknown>
    const name = typeof b.name === "string" ? b.name.trim() : ""
    if (!name) return json({ ok: false, message: "name required" }, { status: 400 })
    const stored = await connectionByName(name)
    if (!stored) {
      return json(
        { ok: false, message: `no connection named "${name}"` },
        { status: 404 },
      )
    }
    const { name: _n, database: _d, ...config } = stored
    // Reset the database so `connect <name>` always lands on the picker.
    const built = await buildSession(name, config, null)
    if (!built.ok) return json({ ok: false, message: built.message })
    sessions.set(sid, built.session)
    touchConnection(name)
    return json({ ok: true, name, databases: built.session.databases })
  }

  // Select this session's active connection's database.
  if (req.method === "POST" && pathname === "/api/clickhouse/database") {
    const s = sessions.get(sid)
    if (!s) {
      return json({ ok: false, message: "not connected" }, { status: 409 })
    }
    const b = (await readJson(req) ?? {}) as Record<string, unknown>
    const database = typeof b.database === "string" ? b.database : ""
    if (!database || !s.databases.includes(database)) {
      return json({ ok: false, message: "unknown database" }, { status: 400 })
    }
    s.database = database
    saveSelectedDatabase(s.name, database)
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
