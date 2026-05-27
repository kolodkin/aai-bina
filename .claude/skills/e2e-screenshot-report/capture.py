#!/usr/bin/env python3
"""Drive QueryView's e2e UI flows in a headless browser, capture labeled
screenshots, and bundle them into one self-contained index.html (images embedded
as base64, so the HTML stands alone).

The flows mirror e2e/test_query.py — keep them in sync when the tests change.

Prerequisites (see SKILL.md for the full runbook):
  - A ClickHouse server reachable at CLICKHOUSE_HOST/PORT (this script seeds a
    `test` database with a known `items` table).
  - The app served at --base-url (backend with SERVE_STATIC=1, or the Vite dev
    server). The built SPA must be current.
  - Playwright chromium installed (uv run --group test playwright install chromium).

Run:  uv run --group test python .claude/skills/e2e-screenshot-report/capture.py
"""

from __future__ import annotations

import argparse
import base64
import html
import os
from collections import OrderedDict
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

CH_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CH_PORT = os.environ.get("CLICKHOUSE_PORT", "8123")
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")

captures: list[tuple[str, str, Path]] = []  # (test_name, label, png_path)
_shots_dir: Path = Path("/tmp")


def ch_exec(sql: str) -> None:
    httpx.post(
        f"http://{CH_HOST}:{CH_PORT}/",
        content=sql.encode(),
        auth=(CH_USER, CH_PASSWORD),
        timeout=10.0,
    ).raise_for_status()


def seed() -> None:
    """Idempotent: a `test` database with a known 3-row `items` table."""
    ch_exec("DROP DATABASE IF EXISTS test")
    ch_exec("CREATE DATABASE test")
    ch_exec("CREATE TABLE test.items (id UInt32, name String) ENGINE = MergeTree ORDER BY id")
    ch_exec("INSERT INTO test.items (id, name) VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')")


def shot(page, test: str, label: str) -> None:
    n = sum(1 for t, _, _ in captures if t == test) + 1
    path = _shots_dir / f"{test}__{n:02d}_{label}.png"
    page.screenshot(path=str(path), full_page=True)
    captures.append((test, label, path))


def open_panel(page) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    page.get_by_test_id("ch-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()


def flow_query_against_seeded_db(page) -> None:
    t = "test_query_against_seeded_db"
    page.goto("/", wait_until="networkidle")
    shot(page, t, "landing prompt")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    shot(page, t, "new connection form")
    page.get_by_test_id("ch-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    shot(page, t, "database picker")
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()
    shot(page, t, "empty query panel")

    page.get_by_test_id("query-input").fill("SELECT name FROM items ORDER BY id")
    select = page.get_by_test_id("query-predefined-select")
    page.once("dialog", lambda d: d.accept("all items"))
    select.select_option("::new::")  # the "+ New name…" item -> name prompt
    page.get_by_test_id("query-save").click()
    expect(select.locator('option[value="all items"]')).to_have_count(1)
    shot(page, t, "saved predefined query")

    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output).to_contain_text("alpha")
    shot(page, t, "results page 1 (limit 2)")

    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    shot(page, t, "results page 2 (next)")


def flow_field_pickers_visibility_and_order_by(page) -> None:
    t = "test_field_pickers_visibility_and_order_by"
    open_panel(page)
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")

    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    shot(page, t, "fields described - both pickers")

    page.get_by_test_id("query-size-min").click()
    shot(page, t, "Min size - textarea collapsed")
    page.get_by_test_id("query-size-s").click()

    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output.locator("table thead th")).to_have_count(2)
    shot(page, t, "results with all columns")

    page.locator('[data-testid="field-toggle"][data-col="id"]').click()
    expect(output.locator("table thead th")).to_have_count(1)
    shot(page, t, "id column hidden (client-side)")

    page.get_by_test_id("fields-clear").click()
    expect(output.locator("table thead th")).to_have_count(0)
    shot(page, t, "clear all - no columns")

    page.get_by_test_id("fields-select-all").click()
    expect(output.locator("table thead th")).to_have_count(2)
    shot(page, t, "select all - columns restored")

    page.locator('[data-testid="orderby-add"][data-col="name"]').click()
    chip = page.locator('[data-testid="orderby-chip"][data-col="name"]')
    expect(chip).to_be_visible()
    chip.get_by_test_id("orderby-dir").click()  # ASC -> DESC
    expect(chip.get_by_test_id("orderby-dir")).to_have_text("DESC")
    shot(page, t, "order by name DESC selected")

    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("orderby-run").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
    shot(page, t, "ordered + limited results (order-by Run)")


FLOWS = (flow_query_against_seeded_db, flow_field_pickers_visibility_and_order_by)

CSS = """
:root{color-scheme:light}*{box-sizing:border-box}
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,
 Helvetica,Arial,sans-serif;background:#0f172a;color:#e2e8f0}
header.top{position:sticky;top:0;z-index:10;background:#0f172a;border-bottom:1px
 solid #1e293b;padding:18px 28px}
header.top h1{margin:0;font-size:20px}header.top p{margin:6px 0 0;color:#94a3b8;
 font-size:13px}
nav{padding:16px 28px;border-bottom:1px solid #1e293b}
nav h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#64748b;
 margin:0 0 8px}
nav details{margin-bottom:10px}nav summary{cursor:pointer;color:#cbd5e1;
 font-weight:600;font-size:14px}
nav a{display:block;color:#7dd3fc;text-decoration:none;font-size:13px;padding:3px 0
 3px 16px}nav a:hover{text-decoration:underline}
main{padding:0 28px 80px}
section.page{min-height:92vh;border-bottom:1px solid #1e293b;padding:28px 0 40px;
 display:flex;flex-direction:column}
.label{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:baseline;margin-bottom:14px}
.test{font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#fbbf24;
 background:#1e293b;padding:4px 10px;border-radius:6px}
.name{font-size:22px;font-weight:700}
.step{color:#64748b;font-size:13px;margin-left:auto;font-family:ui-monospace,monospace}
.frame{background:#fff;border-radius:10px;overflow:hidden;border:1px solid #334155;
 box-shadow:0 10px 30px rgba(0,0,0,.35)}
.frame img{display:block;width:100%;height:auto}
"""


def build_html(base_url: str, out_dir: Path) -> Path:
    groups: "OrderedDict[str, list[tuple[str, Path]]]" = OrderedDict()
    for test, label, path in captures:
        groups.setdefault(test, []).append((label, path))

    nav = ["<nav><h2>Contents</h2>"]
    body = ["<main>"]
    idx = 0
    for test, items in groups.items():
        nav.append(f"<details open><summary>{html.escape(test)}</summary>")
        for label, path in items:
            idx += 1
            anchor = f"shot{idx}"
            nav.append(f'<a href="#{anchor}">{idx:02d}. {html.escape(label)}</a>')
            b64 = base64.b64encode(path.read_bytes()).decode()
            body.append(
                f'<section class="page" id="{anchor}">'
                f'<div class="label"><span class="test">{html.escape(test)}</span>'
                f'<span class="name">{html.escape(label)}</span>'
                f'<span class="step">screenshot {idx:02d}</span></div>'
                f'<div class="frame"><img alt="{html.escape(label)}" '
                f'src="data:image/png;base64,{b64}"></div></section>'
            )
        nav.append("</details>")
    nav.append("</nav>")
    body.append("</main>")

    doc = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>QueryView e2e screenshots</title>"
        f"<style>{CSS}</style></head><body>"
        "<header class=top><h1>QueryView - e2e screenshots</h1>"
        f"<p>{len(captures)} screenshots across {len(groups)} flows - "
        f"base URL {html.escape(base_url)}</p></header>"
        + "".join(nav) + "".join(body) + "</body></html>"
    )
    out = out_dir / "index.html"
    out.write_text(doc, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    ap.add_argument("--out", default=os.environ.get("OUT_DIR", "/tmp/qv-e2e-report"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    global _shots_dir
    _shots_dir = out_dir / "shots"
    _shots_dir.mkdir(parents=True, exist_ok=True)
    expect.set_options(timeout=15_000)

    try:
        httpx.get(f"{args.base_url}/api/health", timeout=5.0).raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"app not reachable at {args.base_url} ({e}). Start it first - see SKILL.md."
        )

    seed()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        for flow in FLOWS:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900}, base_url=args.base_url
            )
            page = ctx.new_page()
            try:
                flow(page)
            finally:
                ctx.close()
        browser.close()

    report = build_html(args.base_url, out_dir)
    print(f"captured {len(captures)} screenshots")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
