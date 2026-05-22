// ClickHouse driver: how to talk to ClickHouse and the shape of a connection
// config. No HTTP-server or storage concerns live here.

export type ChConfig = {
  host: string
  port: number
  username: string
  password: string
}

type QueryResult = { ok: true; text: string } | { ok: false; message: string }

/** Run a query against the ClickHouse HTTP interface (Basic auth, 5s timeout). */
export async function chQuery(c: ChConfig, query: string): Promise<QueryResult> {
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

/** Validate a ClickHouse config from a request body. Returns config or a message. */
export function parseChConfig(
  body: unknown,
): { config: ChConfig } | { error: string } {
  const b = (body ?? {}) as Record<string, unknown>
  const host = typeof b.host === "string" ? b.host.trim() : ""
  const port = typeof b.port === "number"
    ? b.port
    : typeof b.port === "string"
    ? Number(b.port)
    : NaN
  const username = typeof b.username === "string" ? b.username : ""
  const password = typeof b.password === "string" ? b.password : ""
  if (!host) return { error: "host required" }
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    return { error: "valid port required" }
  }
  return { config: { host, port, username, password } }
}

/** Test only: a throwaway connectivity check. */
export async function testConnection(
  c: ChConfig,
): Promise<{ ok: boolean; message: string }> {
  const r = await chQuery(c, "SELECT 1")
  return r.ok
    ? { ok: true, message: `Connected — SELECT 1 returned ${r.text}` }
    : { ok: false, message: r.message }
}

/** List the connection's databases. */
export async function listDatabases(
  c: ChConfig,
): Promise<{ ok: true; databases: string[] } | { ok: false; message: string }> {
  const r = await chQuery(c, "SHOW DATABASES")
  if (!r.ok) return { ok: false, message: r.message }
  const databases = r.text.split("\n").map((s) => s.trim()).filter(Boolean)
  return { ok: true, databases }
}
