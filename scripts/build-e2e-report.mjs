// Builds a self-contained static HTML report from Playwright JUnit XML files
// and captured stdout logs. Mirrors the role of `playwright merge-reports` for
// the queryview e2e workflow: the input dir holds per-shard artifacts, the
// output dir gets an index.html + the raw report sources copied alongside.
//
// Dependency-free Node ESM: run with `node scripts/build-e2e-report.mjs <in> <out>`.

import { readFile, readdir, mkdir, copyFile, writeFile } from "node:fs/promises"
import { join, sep } from "node:path"

const [inputDir, outputDir] = process.argv.slice(2)
if (!inputDir || !outputDir) {
  console.error("usage: build-e2e-report.mjs <input-dir> <output-dir>")
  process.exit(2)
}

await mkdir(outputDir, { recursive: true })

/** @type {Array<{suite:string,name:string,time:number,status:"passed"|"failed"|"skipped",message?:string,details?:string}>} */
const cases = []
/** @type {Array<{name:string,content:string}>} */
const logs = []
/** @type {Array<{group:string,name:string,href:string}>} */
const screenshots = []
const IMG_EXT = /\.(png|jpe?g|gif|webp)$/i

async function walk(dir, relRoot) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    const rel = relRoot ? `${relRoot}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      await mkdir(join(outputDir, rel.replaceAll("/", sep)), { recursive: true })
      await walk(abs, rel)
      continue
    }
    if (!entry.isFile()) continue
    if (entry.name.endsWith(".xml")) {
      cases.push(...parseJUnit(await readFile(abs, "utf8")))
    } else if (entry.name.endsWith(".log")) {
      logs.push({ name: rel, content: await readFile(abs, "utf8") })
    } else if (IMG_EXT.test(entry.name)) {
      screenshots.push({ group: relRoot || "(root)", name: entry.name, href: rel })
    }
    // Copy raw artifacts alongside the rendered report so users can drill in.
    await copyFile(abs, join(outputDir, rel.replaceAll("/", sep)))
  }
}

try {
  await walk(inputDir, "")
} catch (err) {
  if (err?.code !== "ENOENT") throw err
  console.warn(`input dir ${inputDir} not found — emitting an empty report`)
}

screenshots.sort((a, b) =>
  a.group === b.group ? a.name.localeCompare(b.name) : a.group.localeCompare(b.group)
)

const totals = cases.reduce(
  (acc, c) => {
    acc.total++
    acc[c.status]++
    acc.time += c.time
    return acc
  },
  { total: 0, passed: 0, failed: 0, skipped: 0, time: 0 },
)

const html = renderHtml(cases, totals, logs, screenshots)
await writeFile(join(outputDir, "index.html"), html)

console.log(
  `Wrote report to ${outputDir}/index.html — ${totals.passed} passed, ` +
    `${totals.failed} failed, ${totals.skipped} skipped (${totals.total} total), ` +
    `${screenshots.length} screenshot(s)`,
)

// ---- JUnit parsing -------------------------------------------------------

function parseJUnit(xml) {
  const out = []
  const suiteRegex = /<testsuite\b([^>]*)>([\s\S]*?)<\/testsuite>/g
  let suiteMatch
  while ((suiteMatch = suiteRegex.exec(xml)) !== null) {
    const suiteAttrs = parseAttrs(suiteMatch[1])
    const suiteName = suiteAttrs.name ?? "(unnamed suite)"
    const body = suiteMatch[2]
    const caseRegex = /<testcase\b([^>]*?)(\/>|>([\s\S]*?)<\/testcase>)/g
    let caseMatch
    while ((caseMatch = caseRegex.exec(body)) !== null) {
      const attrs = parseAttrs(caseMatch[1])
      const inner = caseMatch[3] ?? ""
      const failure = /<failure\b([^>]*)(?:\/>|>([\s\S]*?)<\/failure>)/.exec(inner)
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

function parseAttrs(s) {
  const attrs = {}
  const re = /(\w+)="([^"]*)"/g
  let m
  while ((m = re.exec(s)) !== null) attrs[m[1]] = decode(m[2])
  return attrs
}

function decode(s) {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
}

// ---- HTML rendering ------------------------------------------------------

function esc(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

function renderHtml(cases, totals, logs, screenshots) {
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

  const groups = new Map()
  for (const s of screenshots) {
    const arr = groups.get(s.group) ?? []
    arr.push(s)
    groups.set(s.group, arr)
  }
  const screenshotsHtml = screenshots.length === 0 ? "" : `
    <h2>Screenshots</h2>
    ${[...groups.entries()].map(([group, items]) => `
      <details open>
        <summary>${esc(group)} <span class="count">(${items.length})</span></summary>
        <div class="gallery">
          ${items.map((s) => `
            <figure>
              <a href="${esc(s.href)}" target="_blank" rel="noopener">
                <img src="${esc(s.href)}" alt="${esc(s.name)}" loading="lazy">
              </a>
              <figcaption>${esc(s.name)}</figcaption>
            </figure>
          `).join("")}
        </div>
      </details>
    `).join("")}
  `

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
  .count { color: #888; font-weight: 400; font-size: 12px; }
  .gallery { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); margin-top: 12px; }
  .gallery figure { margin: 0; background: #f3f4f6; border-radius: 6px; overflow: hidden; }
  .gallery img { display: block; width: 100%; height: auto; }
  .gallery figcaption { padding: 6px 10px; font-size: 12px; color: #555; word-break: break-all; }
  @media (prefers-color-scheme: dark) {
    body { background: #0d1117; color: #e6edf3; }
    .card, table, details { background: #161b22; box-shadow: none; border: 1px solid #30363d; }
    th { background: #1f2933; }
    th, td { border-color: #30363d; }
    .meta, .lbl, .time { color: #8b949e; }
    .details td { background: #1c1417; }
    .details.passed td { background: #0e1f15; }
    .gallery figure { background: #161b22; border: 1px solid #30363d; }
    .gallery figcaption { color: #8b949e; }
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
  ${screenshotsHtml}
  ${logsHtml ? `<h2>Logs</h2>${logsHtml}` : ""}
</body>
</html>`
}
