// The dashboard iframe is sandboxed with `allow-scripts` alone, so its page JS
// has a null origin and cannot call /api itself. Instead it postMessages a
// run-queries request to the host, which runs the SQL against the dashboard's
// connection and posts the results back — letting a dashboard query on demand
// (a dropdown change) rather than only from the results frozen in at load.

// Column-oriented results map: {query_name: {column_name: values[]}}.
export type Results = Record<string, Record<string, unknown[]>>

// Resolved selector handed to the page: its option list and current value, so
// the page can render controls without knowing how they were resolved.
export type { ResolvedParam } from './dashboardParams'

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

// The in-iframe half of the params channel. The page sends values only; the host
// substitutes them into the dashboard's own SQL and posts results back, so the
// page re-renders without a reload and never handles SQL.
const SET_PARAMS_HELPER = `
window.setParams = function (values) {
  parent.postMessage({ type: 'set-params', values: values }, '*');
};
window.addEventListener('message', function (e) {
  var d = e.data;
  if (!d || d.type !== 'params-results') return;
  // A failed run keeps the previous results on screen and reports the message.
  if (d.results) window.queries = d.results;
  if (d.params && d.params.length) window.params = d.params;
  if (typeof window.onQueryResults === 'function') {
    window.onQueryResults(window.queries, window.params, d.ok ? null : d.message);
  }
});`

// The iframe document: a prologue exposing results as `window.queries`, the
// resolved selectors as `window.params`, and the bridge helpers, then the
// agent-authored HTML. JSON `<` is escaped so an embedded closing script tag in
// result data can't break out of the prologue.
export function buildSrcDoc(html: string, results: Results, params: ResolvedParam[] = []): string {
  const safe = (v: unknown) => JSON.stringify(v).replace(/</g, '\\u003c')
  return (
    `<script>window.queries = ${safe(results)};\nwindow.params = ${safe(params)};` +
    `${RUN_QUERIES_HELPER}${SET_PARAMS_HELPER}\n</script>\n${html}`
  )
}

// A set-params message: the page asks for its declared queries to be re-run with
// new selections. Values only — the SQL lives in the dashboard's queries and is
// substituted host-side, so the page never composes or issues SQL of its own.
export function parseParamsRequest(data: unknown): Record<string, string> | null {
  if (typeof data !== 'object' || data === null) return null
  const msg = data as Record<string, unknown>
  if (msg.type !== 'set-params') return null
  const values = msg.values
  if (typeof values !== 'object' || values === null || Array.isArray(values)) return null
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(values as Record<string, unknown>)) {
    // Checkboxes arrive as booleans; substituteParams reads '' as unset.
    out[k] = typeof v === 'boolean' ? (v ? 'on' : '') : String(v)
  }
  return out
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
