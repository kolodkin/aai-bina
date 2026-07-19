// The dashboard iframe is sandboxed with `allow-scripts` alone, so its page JS
// has a null origin and cannot call /api itself. Instead it postMessages a
// run-queries request to the host, which runs the SQL against the dashboard's
// connection and posts the results back — letting a dashboard query on demand
// (a dropdown change) rather than only from the results frozen in at load.

// Column-oriented results map: {query_name: {column_name: values[]}}.
export type Results = Record<string, Record<string, unknown[]>>

export type BridgeRequest = { id: string; queries: Record<string, string> }

export type BridgeAnswer =
  | { type: 'query-results'; id: string; ok: true; results: Results }
  | { type: 'query-results'; id: string; ok: false; message: string }

export type QueryRunner = (
  queries: Record<string, string>,
) => Promise<{ ok: true; results: Results } | { ok: false; message: string }>

// A run-queries message from the iframe, or null for anything else — the host
// listens on `window`, so every other page's messages land here too.
export function parseBridgeRequest(data: unknown): BridgeRequest | null {
  if (typeof data !== 'object' || data === null) return null
  const msg = data as Record<string, unknown>
  if (msg.type !== 'run-queries' || typeof msg.id !== 'string') return null
  if (typeof msg.queries !== 'object' || msg.queries === null) return null
  const queries = msg.queries as Record<string, unknown>
  const entries = Object.entries(queries)
  if (entries.some(([, sql]) => typeof sql !== 'string')) return null
  return { id: msg.id, queries: Object.fromEntries(entries) as Record<string, string> }
}

// The in-iframe half of the bridge: window.runQueries(queries) -> Promise of a
// results map. Correlated by id so overlapping calls can't cross their answers.
const RUN_QUERIES_HELPER = `
window.runQueries = function (queries) {
  return new Promise(function (resolve, reject) {
    var id = 'q' + (window.__qvSeq = (window.__qvSeq || 0) + 1);
    function onAnswer(e) {
      var d = e.data;
      if (!d || d.type !== 'query-results' || d.id !== id) return;
      window.removeEventListener('message', onAnswer);
      if (d.ok) resolve(d.results); else reject(new Error(d.message));
    }
    window.addEventListener('message', onAnswer);
    parent.postMessage({ type: 'run-queries', id: id, queries: queries }, '*');
  });
};`

// The iframe document: a prologue exposing results as `window.queries` and the
// runQueries bridge, then the agent-authored HTML. JSON `<` is escaped so an
// embedded closing script tag in result data can't break out of the prologue.
export function buildSrcDoc(html: string, results: Results): string {
  const safeJson = JSON.stringify(results).replace(/</g, '\\u003c')
  return `<script>window.queries = ${safeJson};${RUN_QUERIES_HELPER}\n</script>\n${html}`
}

export async function answerBridgeRequest(
  req: BridgeRequest,
  run: QueryRunner,
): Promise<BridgeAnswer> {
  try {
    const r = await run(req.queries)
    return r.ok
      ? { type: 'query-results', id: req.id, ok: true, results: r.results }
      : { type: 'query-results', id: req.id, ok: false, message: r.message }
  } catch {
    return { type: 'query-results', id: req.id, ok: false, message: 'Failed to run queries.' }
  }
}
