// Connection domain: the SQLite connection store (passwords encrypted at rest)
// and per-session active connections. No HTTP-server concerns live here; the
// operations return plain results and main.ts maps them to responses.

import { fromFileUrl } from "jsr:@std/path@^1.0.0"
import { decodeBase64, encodeBase64 } from "jsr:@std/encoding@^1/base64"
import { DatabaseSync } from "node:sqlite"
import { type ChConfig, listDatabases } from "./clickhouse.ts"

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

// Cap the session map and evict the least-recently-used entry so it can't grow
// unbounded (every fresh cookie adds one). An evicted session transparently
// rebuilds on its next request via ensureSession. Map keeps insertion order, so
// re-inserting on access moves an entry to the most-recently-used end.
const MAX_SESSIONS = Number(Deno.env.get("MAX_SESSIONS") ?? 1000)

function getSessionEntry(sid: string): Session | undefined {
  const s = sessions.get(sid)
  if (s !== undefined) {
    sessions.delete(sid)
    sessions.set(sid, s)
  }
  return s
}

function setSessionEntry(sid: string, s: Session): void {
  sessions.delete(sid)
  sessions.set(sid, s)
  if (sessions.size > MAX_SESSIONS) {
    const oldest = sessions.keys().next().value
    if (oldest !== undefined) sessions.delete(oldest)
  }
}

// List a connection's databases and build a session object.
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
  if (getSessionEntry(sid)) return
  const stored = await latestActiveConnection()
  if (!stored) return
  const { name, database, ...config } = stored
  const built = await buildSession(name, config, database)
  if (built.ok) setSessionEntry(sid, built.session)
}

export type SessionView =
  | { connected: true; name: string; databases: string[]; database: string | null }
  | { connected: false }

/** This session's state; auto-connects the latest active for an unseen cookie. */
export async function getSession(sid: string): Promise<SessionView> {
  await ensureSession(sid)
  const s = getSessionEntry(sid)
  return s
    ? { connected: true, name: s.name, databases: s.databases, database: s.database }
    : { connected: false }
}

type OpenResult =
  | { ok: true; name: string; databases: string[] }
  | { ok: false; message: string; notFound?: boolean }

/** Create: open a config, save + activate it for this session. */
export async function connectNew(
  sid: string,
  name: string,
  config: ChConfig,
): Promise<OpenResult> {
  const built = await buildSession(name, config, null)
  if (!built.ok) return { ok: false, message: built.message }
  setSessionEntry(sid, built.session)
  await saveActiveConnection(name, config)
  return { ok: true, name, databases: built.session.databases }
}

/** Open a saved connection by name for this session. */
export async function openSaved(sid: string, name: string): Promise<OpenResult> {
  const stored = await connectionByName(name)
  if (!stored) {
    return { ok: false, message: `no connection named "${name}"`, notFound: true }
  }
  const { name: _n, database: _d, ...config } = stored
  // Reset the database so `connect <name>` always lands on the picker.
  const built = await buildSession(name, config, null)
  if (!built.ok) return { ok: false, message: built.message }
  setSessionEntry(sid, built.session)
  touchConnection(name)
  return { ok: true, name, databases: built.session.databases }
}

/** Select this session's active connection's database. */
export function selectDatabase(
  sid: string,
  database: string,
): { ok: true } | { ok: false; message: string; reason: "no-session" | "unknown" } {
  const s = getSessionEntry(sid)
  if (!s) return { ok: false, message: "not connected", reason: "no-session" }
  if (!database || !s.databases.includes(database)) {
    return { ok: false, message: "unknown database", reason: "unknown" }
  }
  s.database = database
  saveSelectedDatabase(s.name, database)
  return { ok: true }
}
