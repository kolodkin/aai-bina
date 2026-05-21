import { serveDir, serveFile } from "jsr:@std/http@^1.0.0/file-server"
import { fromFileUrl, join } from "jsr:@std/path@^1.0.0"
import { decodeBase64, encodeBase64 } from "jsr:@std/encoding@^1/base64"
import { DatabaseSync } from "node:sqlite"

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

type ChConfig = { host: string; port: number; username: string; password: string }
type ChQueryResult = { ok: true; text: string } | { ok: false; message: string }

async function chQuery(c: ChConfig, query: string): Promise<ChQueryResult> {
  const url = `http://${c.host}:${c.port}/?query=${encodeURIComponent(query)}`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 5000)
  try {
    const res = await fetch(url, {
      headers: { Authorization: `Basic ${btoa(`${c.username}:${c.password}`)}` },
      signal: controller.signal,
    })
    const text = (await res.text()).trim()
    if (!res.ok) {
      return {
        ok: false,
        message: `ClickHouse responded ${res.status}: ${text.slice(0, 200)}`,
      }
    }
    return { ok: true, text }
  } catch (err) {
    const message = err instanceof Error
      ? (err.name === "AbortError" ? "connection timed out" : err.message)
      : "connection failed"
    return { ok: false, message }
  } finally {
    clearTimeout(timeout)
  }
}

function parseChConfig(
  body: unknown,
): { config: ChConfig } | { error: Response } {
  const b = (body ?? {}) as Record<string, unknown>
  const host = typeof b.host === "string" ? b.host.trim() : ""
  const port = typeof b.port === "number"
    ? b.port
    : typeof b.port === "string"
    ? Number(b.port)
    : NaN
  const username = typeof b.username === "string" ? b.username : ""
  const password = typeof b.password === "string" ? b.password : ""
  if (!host) {
    return { error: json({ ok: false, message: "host required" }, { status: 400 }) }
  }
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    return {
      error: json({ ok: false, message: "valid port required" }, { status: 400 }),
    }
  }
  return { config: { host, port, username, password } }
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

async function latestActiveConnection(): Promise<StoredConnection | null> {
  const row = db.prepare(
    `SELECT name, host, port, username, password, database
     FROM connections ORDER BY last_active_at DESC LIMIT 1`,
  ).get() as Record<string, unknown> | undefined
  if (!row) return null
  let password: string
  try {
    password = await decryptPassword(String(row.password))
  } catch {
    // Unreadable (key changed / legacy plaintext) — skip auto-connect.
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

// --- Session (one active connection, held in memory) ----------------------
type Session = {
  name: string
  config: ChConfig
  databases: string[]
  database: string | null
}
let session: Session | null = null

// Open a steady connection: list its databases and make it the active session.
async function openConnection(
  name: string,
  config: ChConfig,
  database: string | null,
): Promise<{ ok: true } | { ok: false; message: string }> {
  const r = await chQuery(config, "SHOW DATABASES")
  if (!r.ok) return { ok: false, message: r.message }
  const databases = r.text.split("\n").map((s) => s.trim()).filter(Boolean)
  session = {
    name,
    config,
    databases,
    database: database && databases.includes(database) ? database : null,
  }
  return { ok: true }
}

// At session start, attempt to reconnect the latest active connection.
async function ensureSession(): Promise<void> {
  if (session) return
  const stored = await latestActiveConnection()
  if (!stored) return
  const { name, database, ...config } = stored
  await openConnection(name, config, database)
}

function sessionState(): Record<string, unknown> {
  return session
    ? {
      connected: true,
      name: session.name,
      databases: session.databases,
      database: session.database,
    }
    : { connected: false }
}

async function handleApi(req: Request, pathname: string): Promise<Response | null> {
  if (req.method === "GET" && pathname === "/api/health") {
    return json({ status: "ok", service: "queryview-backend" })
  }

  if (req.method === "GET" && pathname === "/api/session") {
    await ensureSession()
    return json(sessionState())
  }

  // Test only: a throwaway connectivity check, no save, no activation.
  if (req.method === "POST" && pathname === "/api/clickhouse/test") {
    const parsed = parseChConfig(await readJson(req))
    if ("error" in parsed) return parsed.error
    const r = await chQuery(parsed.config, "SELECT 1")
    return json(
      r.ok
        ? { ok: true, message: `Connected — SELECT 1 returned ${r.text}` }
        : { ok: false, message: r.message },
    )
  }

  // Open a steady connection: list databases, persist, and activate it.
  if (req.method === "POST" && pathname === "/api/clickhouse/connect") {
    const body = await readJson(req)
    const parsed = parseChConfig(body)
    if ("error" in parsed) return parsed.error
    const b = (body ?? {}) as Record<string, unknown>
    const name = typeof b.name === "string" && b.name.trim()
      ? b.name.trim()
      : "clickhouse"
    const opened = await openConnection(name, parsed.config, null)
    if (!opened.ok) return json({ ok: false, message: opened.message })
    await saveActiveConnection(name, parsed.config)
    return json({ ok: true, name, databases: session!.databases })
  }

  // Select the active connection's database.
  if (req.method === "POST" && pathname === "/api/clickhouse/database") {
    if (!session) {
      return json({ ok: false, message: "not connected" }, { status: 409 })
    }
    const b = (await readJson(req) ?? {}) as Record<string, unknown>
    const database = typeof b.database === "string" ? b.database : ""
    if (!database || !session.databases.includes(database)) {
      return json({ ok: false, message: "unknown database" }, { status: 400 })
    }
    session.database = database
    saveSelectedDatabase(session.name, database)
    return json({ ok: true })
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
