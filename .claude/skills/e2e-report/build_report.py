#!/usr/bin/env python3
"""Build a single consolidated e2e artifact from Astral screenshots.

Each screenshot is shown under its humanized step title (derived from the file
name, e.g. ``05-selecting-a-database...png`` -> "5. Selecting a database..."),
matching the step names from the test run. Output is a self-contained HTML file
(images embedded as base64, so it is portable as a single artifact); a PDF is
also rendered when a Chrome/Chromium binary is available.

Usage:
  python build_report.py [--screenshots DIR] [--out FILE]
                         [--title TITLE] [--pdf] [--no-pdf]

Defaults: screenshots from $SCREENSHOT_DIR or the first existing of
.cache/screenshots, e2e/screenshots, ./screenshots; output to
.cache/e2e-report/index.html.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
DEFAULT_SCREENSHOT_DIRS = (".cache/screenshots", "e2e/screenshots", "screenshots")
LEADING_INDEX = re.compile(r"^(\d+)[-_\s]+")


def find_screenshots_dir(explicit: str | None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("SCREENSHOT_DIR")
    if env:
        candidates.append(env)
    candidates.extend(DEFAULT_SCREENSHOT_DIRS)
    for c in candidates:
        p = Path(c)
        if p.is_dir() and any(f.suffix.lower() in IMAGE_EXTS for f in p.iterdir()):
            return p
    return None


def humanize(filename: str) -> tuple[str | None, str]:
    """Return (step_number, title) from a screenshot file name."""
    stem = Path(filename).stem
    m = LEADING_INDEX.match(stem)
    number = None
    if m:
        number = str(int(m.group(1)))  # drop zero-padding
        stem = stem[m.end():]
    words = re.split(r"[-_\s]+", stem)
    title = " ".join(w for w in words if w).strip()
    title = title[:1].upper() + title[1:] if title else "Step"
    return number, title


def find_chrome() -> str | None:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


def data_uri(path: Path) -> str:
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_html(images: list[Path], title: str) -> str:
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    steps_html = []
    for img in images:
        number, step_title = humanize(img.name)
        label = f"{number}. {step_title}" if number else step_title
        steps_html.append(
            f"""    <section class="step">
      <h2><span class="num">{html.escape(number or '')}</span>{html.escape(step_title)}</h2>
      <img alt="{html.escape(label)}" src="{data_uri(img)}" />
    </section>"""
        )
    body = "\n".join(steps_html)
    count = len(images)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.5rem 4rem;
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #0f172a; background: #f8fafc;
  }}
  header {{ max-width: 960px; margin: 0 auto 2rem; }}
  h1 {{ font-size: 2rem; letter-spacing: -0.02em; margin: 0 0 .25rem; }}
  .meta {{ color: #64748b; font-size: .9rem; }}
  .step {{
    max-width: 960px; margin: 0 auto 1.5rem; padding: 1.25rem;
    background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
    box-shadow: 0 1px 2px rgba(15,23,42,.04);
    break-inside: avoid; page-break-inside: avoid;
  }}
  .step h2 {{
    display: flex; align-items: center; gap: .6rem;
    font-size: 1.05rem; margin: 0 0 .9rem; color: #1e293b;
  }}
  .num {{
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 1.8rem; height: 1.8rem; padding: 0 .4rem;
    background: #4f46e5; color: #fff; border-radius: 999px;
    font-size: .85rem; font-weight: 600;
  }}
  .num:empty {{ display: none; }}
  .step img {{
    display: block; width: 100%; height: auto;
    border: 1px solid #e2e8f0; border-radius: 10px;
  }}
  footer {{ max-width: 960px; margin: 2rem auto 0; color: #94a3b8; font-size: .8rem; text-align: center; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .step {{ box-shadow: none; }}
  }}
</style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <p class="meta">{count} step{'s' if count != 1 else ''} &middot; generated {generated}</p>
  </header>
{body}
  <footer>QueryView e2e artifact</footer>
</body>
</html>
"""


def render_pdf(html_path: Path, pdf_path: Path, chrome: str) -> bool:
    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as err:
            print(f"PDF render skipped: {err}", file=sys.stderr)
            return False
    if not pdf_path.exists():
        # Older Chrome rejects --headless=new / --no-pdf-header-footer; retry plainly.
        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return pdf_path.exists()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", help="directory of e2e screenshots")
    parser.add_argument("--out", default=".cache/e2e-report/index.html",
                        help="output HTML path (PDF written alongside)")
    parser.add_argument("--title", default="QueryView E2E Report")
    pdf = parser.add_mutually_exclusive_group()
    pdf.add_argument("--pdf", action="store_true", help="require a PDF (fail if no Chrome)")
    pdf.add_argument("--no-pdf", action="store_true", help="HTML only, never render a PDF")
    args = parser.parse_args()

    src = find_screenshots_dir(args.screenshots)
    if src is None:
        print(
            "No screenshots found. Run the e2e suite first (e.g. scripts/setup.sh "
            "or deno task test:e2e) or pass --screenshots DIR.",
            file=sys.stderr,
        )
        return 1

    images = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(images, args.title), encoding="utf-8")
    print(f"HTML  -> {out}  ({len(images)} screenshots from {src})")

    if not args.no_pdf:
        chrome = find_chrome()
        if chrome:
            pdf_path = out.with_suffix(".pdf")
            if render_pdf(out, pdf_path, chrome):
                print(f"PDF   -> {pdf_path}")
            elif args.pdf:
                print("PDF render failed.", file=sys.stderr)
                return 1
        elif args.pdf:
            print("No Chrome/Chromium found for --pdf (set CHROME_PATH).", file=sys.stderr)
            return 1
        else:
            print("PDF skipped: no Chrome/Chromium found (set CHROME_PATH to enable).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
