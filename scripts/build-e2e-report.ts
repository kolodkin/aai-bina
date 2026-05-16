// Builds a self-contained static HTML report from deno-test JUnit XML files
// and captured stdout logs. Mirrors the role of `playwright merge-reports`
// for the queryview e2e workflow: input dir holds per-shard artifacts, output
// dir gets an index.html + the raw report sources copied alongside.

type TestCase = {
  suite: string
  name: string
  time: number
  status: "passed" | "failed" | "skipped"
  message?: string
  details?: string
}

const [inputDir, outputDir] = Deno.args
if (!inputDir || !outputDir) {
  console.error("usage: build-e2e-report.ts <input-dir> <output-dir>")
  Deno.exit(2)
}

await Deno.mkdir(outputDir, { recursive: true })

const cases: TestCase[] = []
const logs: Array<{ name: string; content: string }> = []
const sep = Deno.build.os === "windows" ? "\\" : "/"

for await (const entry of Deno.readDir(inputDir)) {
  if (!entry.isFile) continue
  const path = `${inputDir}${sep}${entry.name}`
  if (entry.name.endsWith(".xml")) {
    const xml = await Deno.readTextFile(path)
    cases.push(...parseJUnit(xml))
  } else if (entry.name.endsWith(".log")) {
    logs.push({ name: entry.name, content: await Deno.readTextFile(path) })
  }
  // Copy raw artifacts alongside the rendered report so users can drill in.
  await Deno.copyFile(path, `${outputDir}${sep}${entry.name}`)
}

const totals = cases.reduce(
  (acc, c) => {
    acc.total++
    acc[c.status]++
    acc.time += c.time
    return acc
  },
  { total: 0, passed: 0, failed: 0, skipped: 0, time: 0 },
)

const html = renderHtml(cases, totals, logs)
await Deno.writeTextFile(`${outputDir}${sep}index.html`, html)

console.log(
  `Wrote report to ${outputDir}/index.html — ${totals.passed} passed, ` +
    `${totals.failed} failed, ${totals.skipped} skipped (${totals.total} total)`,
)

// ---- JUnit parsing -------------------------------------------------------

function parseJUnit(xml: string): TestCase[] {
  const out: TestCase[] = []
  const suiteRegex = /<testsuite\b([^>]*)>([\s\S]*?)<\/testsuite>/g
  let suiteMatch: RegExpExecArray | null
  while ((suiteMatch = suiteRegex.exec(xml)) !== null) {
    const suiteAttrs = parseAttrs(suiteMatch[1])
    const suiteName = suiteAttrs.name ?? "(unnamed suite)"
    const body = suiteMatch[2]
    const caseRegex = /<testcase\b([^>]*?)(\/>|>([\s\S]*?)<\/testcase>)/g
    let caseMatch: RegExpExecArray | null
    while ((caseMatch = caseRegex.exec(body)) !== null) {
      const attrs = parseAttrs(caseMatch[1])
      const inner = caseMatch[3] ?? ""
      const failure = /<failure\b([^>]*)(?:\/>|>([\s\S]*?)<\/failure>)/.exec(
        inner,
      )
      const error = /<error\b([^>]*)(?:\/>|>([\s\S]*?)<\/error>)/.exec(inner)
      const skipped = /<skipped\b[^>]*\/>/.test(inner)
      const problem = failure ?? error
      out.push({
        suite: suiteName,
        name: attrs.name ?? "(unnamed test)",
        time: Number(attrs.time ?? "0") || 0,
        status: problem ? "failed" : skipped ? "skipped" : "passed",
        message: problem ? parseAttrs(problem[1]).message : undefined,
        details: problem?.[2] ? decode(problem[2]).trim() : undefined,
      })
    }
  }
  return out
}

function parseAttrs(s: string): Record<string, string> {
  const attrs: Record<string, string> = {}
  const re = /(\w+)="([^"]*)"/g
  let m: RegExpExecArray | null
  while ((m = re.exec(s)) !== null) attrs[m[1]] = decode(m[2])
  return attrs
}

function decode(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
}

// ---- HTML rendering ------------------------------------------------------

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

function renderHtml(
  cases: TestCase[],
  totals: { total: number; passed: number; failed: number; skipped: number; time: number },
  logs: Array<{ name: string; content: string }>,
): string {
  const rows = cases.map((c) => `
      <tr class="row ${c.status}">
        <td><span class="badge ${c.status}">${c.status}</span></td>
        <td>${esc(c.suite)}</td>
        <td>${esc(c.name)}</td>
        <td class="time">${c.time.toFixed(3)}s</td>
      </tr>
      ${c.details || c.message
      ? `<tr class="details ${c.status}"><td colspan="4"><div class="msg">${
        c.message ? `<strong>${esc(c.message)}</strong>` : ""
      }${c.details ? `<pre>${esc(c.details)}</pre>` : ""}</div></td></tr>`
      : ""}
    `).join("")

  const logsHtml = logs.map((l) =>
    `<details><summary>${esc(l.name)}</summary><pre>${esc(l.content)}</pre></details>`
  ).join("")

  const summaryClass = totals.failed > 0 ? "failed" : "passed"

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>QueryView E2E Report</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px; background: #fafafa; color: #222; }
  h1 { margin: 0 0 4px; }
  .meta { color: #666; margin-bottom: 24px; }
  .summary { display: flex; gap: 12px; margin-bottom: 24px; }
  .card { padding: 12px 18px; border-radius: 8px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card .num { font-size: 22px; font-weight: 600; display: block; }
  .card .lbl { color: #666; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  .card.passed .num { color: #1a7f37; }
  .card.failed .num { color: #cf222e; }
  .card.skipped .num { color: #9a6700; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; }
  th { background: #f3f4f6; font-weight: 600; }
  .time { color: #666; font-variant-numeric: tabular-nums; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
  .badge.passed { background: #dcfce7; color: #166534; }
  .badge.failed { background: #fee2e2; color: #991b1b; }
  .badge.skipped { background: #fef3c7; color: #854d0e; }
  .details td { background: #fff7f7; }
  .details.passed td { background: #f3fdf5; }
  .msg pre { white-space: pre-wrap; background: #0d1117; color: #c9d1d9; padding: 12px; border-radius: 6px; overflow-x: auto; }
  details { margin-top: 12px; background: #fff; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  summary { cursor: pointer; font-weight: 600; }
  pre { white-space: pre-wrap; }
  @media (prefers-color-scheme: dark) {
    body { background: #0d1117; color: #e6edf3; }
    .card, table, details { background: #161b22; box-shadow: none; border: 1px solid #30363d; }
    th { background: #1f2933; }
    th, td { border-color: #30363d; }
    .meta, .lbl, .time { color: #8b949e; }
    .details td { background: #1c1417; }
    .details.passed td { background: #0e1f15; }
  }
</style>
</head>
<body>
  <h1>QueryView E2E Report</h1>
  <div class="meta">${totals.total} tests · ${totals.time.toFixed(2)}s total</div>
  <div class="summary">
    <div class="card ${summaryClass}"><span class="num">${totals.total}</span><span class="lbl">Total</span></div>
    <div class="card passed"><span class="num">${totals.passed}</span><span class="lbl">Passed</span></div>
    <div class="card failed"><span class="num">${totals.failed}</span><span class="lbl">Failed</span></div>
    <div class="card skipped"><span class="num">${totals.skipped}</span><span class="lbl">Skipped</span></div>
  </div>
  <table>
    <thead><tr><th>Status</th><th>Suite</th><th>Test</th><th>Duration</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="4">No test cases found.</td></tr>`}</tbody>
  </table>
  ${logsHtml ? `<h2>Logs</h2>${logsHtml}` : ""}
</body>
</html>`
}
