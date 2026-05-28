#!/usr/bin/env python3
"""Post-process pytest-playwright screenshots into a self-contained HTML report.

Walks a pytest-playwright `--output` directory (default `test-results/`),
collects every PNG (those written by the in-test `shot(label)` fixture in
e2e/conftest.py, plus any auto-shots from `--screenshot=on`), groups by test
nodeid, and writes a single `index.html` with the images base64-embedded so
the file stands alone (no sidecar folder needed to view or share it).

Usage:
    uv run python .claude/skills/e2e-screenshot-report/report.py \\
        --in /tmp/qv-test-results --out /tmp/qv-e2e-report/index.html
"""
from __future__ import annotations

import argparse
import base64
import re
from collections import OrderedDict
from html import escape
from pathlib import Path

# pytest-playwright auto-shot from `--screenshot=on`. Keep it (last, by name).
AUTO_SHOT_RE = re.compile(r"^test-(finished|failed)-\d+$")
# Our `shot(label)` fixture names: "01-landing-prompt", "02-database-picker", …
NUMBERED_RE = re.compile(r"^(\d+)-(.+)$")
BROWSERS = {"chromium", "firefox", "webkit"}


def humanize_test(slug: str) -> str:
    """Turn pytest-playwright's slugified nodeid into a readable label.

    `slugify` joins on '-' and strips path separators, so
        e2e/test_query.py::test_query_against_seeded_db[chromium]
    becomes
        e2e-test-query-py-test-query-against-seeded-db-chromium
    """
    parts = slug.split("-")
    suffix = ""
    if parts and parts[-1].lower() in BROWSERS:
        suffix = f" ({parts.pop().lower()})"
    # `test_query_py` looks better as `test_query.py`
    pretty = " ".join(parts).replace(" py ", ".py ").replace(" py", ".py")
    return pretty + suffix


def humanize_shot(stem: str) -> str:
    """Turn a PNG stem into a label. `01-landing-prompt` -> `landing prompt`."""
    m = NUMBERED_RE.match(stem)
    if m:
        return m.group(2).replace("-", " ")
    if AUTO_SHOT_RE.match(stem):
        # e.g. test-finished-1 -> test finished (auto-shot from --screenshot=on)
        return stem.replace("-", " ")
    return stem.replace("-", " ").replace("_", " ")


def sort_key(p: Path) -> tuple[int, str]:
    """Put numbered shots first (in order), then auto-shots, then anything else."""
    m = NUMBERED_RE.match(p.stem)
    if m:
        return (0, f"{int(m.group(1)):04d}")
    if AUTO_SHOT_RE.match(p.stem):
        return (2, p.stem)
    return (1, p.stem)


def collect(in_dir: Path) -> "OrderedDict[str, list[Path]]":
    """{test_slug: [png, …]} in stable order."""
    groups: dict[str, list[Path]] = {}
    for png in in_dir.rglob("*.png"):
        rel = png.relative_to(in_dir)
        if len(rel.parts) < 2:
            continue
        groups.setdefault(rel.parts[0], []).append(png)
    return OrderedDict(
        (k, sorted(groups[k], key=sort_key)) for k in sorted(groups)
    )


CSS = """
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,
 Helvetica,Arial,sans-serif;background:#0f172a;color:#e2e8f0}
header.top{position:sticky;top:0;z-index:10;background:#0f172a;border-bottom:1px
 solid #1e293b;padding:18px 28px}
header.top h1{margin:0;font-size:20px}
header.top p{margin:6px 0 0;color:#94a3b8;font-size:13px}
nav{padding:16px 28px;border-bottom:1px solid #1e293b}
nav h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#64748b;
 margin:0 0 8px}
nav details{margin-bottom:10px}
nav summary{cursor:pointer;color:#cbd5e1;font-weight:600;font-size:14px}
nav a{display:block;color:#7dd3fc;text-decoration:none;font-size:13px;padding:3px 0
 3px 16px}
nav a:hover{text-decoration:underline}
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


def build_report(groups: "OrderedDict[str, list[Path]]", out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    nav: list[str] = ["<nav><h2>Contents</h2>"]
    body: list[str] = ["<main>"]
    idx = 0
    total = sum(len(v) for v in groups.values())
    for slug, pngs in groups.items():
        test_label = humanize_test(slug)
        nav.append(f"<details open><summary>{escape(test_label)}</summary>")
        for png in pngs:
            idx += 1
            anchor = f"shot{idx}"
            shot_label = humanize_shot(png.stem)
            b64 = base64.b64encode(png.read_bytes()).decode("ascii")
            nav.append(
                f'<a href="#{anchor}">{idx:02d}. {escape(shot_label)}</a>'
            )
            body.append(
                f'<section class="page" id="{anchor}">'
                f'<div class="label">'
                f'<span class="test">{escape(test_label)}</span>'
                f'<span class="name">{escape(shot_label)}</span>'
                f'<span class="step">screenshot {idx:02d}</span>'
                f'</div>'
                f'<div class="frame"><img alt="{escape(shot_label)}" '
                f'src="data:image/png;base64,{b64}"></div>'
                f'</section>'
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
        f"<p>{total} screenshots across {len(groups)} tests</p></header>"
        + "".join(nav) + "".join(body) + "</body></html>"
    )
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="in_dir", required=True, type=Path,
                    help="pytest-playwright --output directory")
    ap.add_argument("--out", type=Path,
                    default=Path("/tmp/qv-e2e-report/index.html"),
                    help="output HTML path (default /tmp/qv-e2e-report/index.html)")
    args = ap.parse_args()

    if not args.in_dir.is_dir():
        raise SystemExit(f"input directory not found: {args.in_dir}")

    groups = collect(args.in_dir)
    total = sum(len(v) for v in groups.values())
    if not total:
        raise SystemExit(
            f"no PNGs found under {args.in_dir} — did pytest run with the "
            "`shot` fixture (or `--screenshot=on`) and the right --output?"
        )

    out = build_report(groups, args.out)
    print(f"captured {total} screenshots across {len(groups)} tests")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
