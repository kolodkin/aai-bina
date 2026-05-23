#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10"]
# ///
"""Build a single consolidated e2e artifact from Astral screenshots.

Each screenshot is shown under its humanized step title (derived from the file
name, e.g. ``05-selecting-a-database...png`` -> "5. Selecting a database..."),
matching the step names from the test run. Output is a self-contained HTML file
(images embedded as base64, so it is portable as a single artifact); a PDF is
also rendered when a Chrome/Chromium binary is available.

Screenshots are downscaled (default max-width 1000px) and re-encoded so the
gallery scrolls smoothly — full-resolution bitmaps are expensive to paint per
frame. This needs Pillow; run via `uv run` to auto-install it (PEP 723 metadata
above), otherwise the originals are embedded at full size.

Usage:
  uv run build_report.py [--screenshots DIR] [--out FILE] [--title TITLE]
                         [--max-width PX] [--image-format png|webp|jpeg|original]
                         [--quality N] [--pdf] [--no-pdf]

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
import struct
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


def image_size(path: Path) -> tuple[int, int] | None:
    """Pixel (width, height) for a PNG, else None (stdlib only — PNG is what the
    Astral suite emits). Used to reserve space so off-screen steps don't reflow."""
    try:
        head = path.read_bytes()[:24]
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        return w, h
    return None


def mime_for(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix.lower(), "application/octet-stream")


def load_pillow():
    """Return PIL.Image if available, else None (run via `uv run` to auto-install)."""
    try:
        from PIL import Image

        return Image
    except ModuleNotFoundError:
        return None


def prepare_image(
    path: Path, max_width: int, fmt: str, quality: int, Image
) -> tuple[bytes, str, tuple[int, int] | None]:
    """Downscale to max_width and re-encode so each embedded image is small enough
    to paint cheaply while scrolling. Falls back to the original bytes when Pillow
    is unavailable or processing fails. Returns (data, mime, (w, h))."""
    raw = path.read_bytes()
    if Image is None or fmt == "original":
        return raw, mime_for(path.suffix), image_size(path)
    try:
        from io import BytesIO

        im = Image.open(BytesIO(raw))
        im.load()
        w, h = im.size
        if max_width and w > max_width:
            im = im.resize(
                (max_width, round(h * max_width / w)),
                getattr(Image, "Resampling", Image).LANCZOS,
            )
            w, h = im.size
        buf = BytesIO()
        if fmt == "jpeg":
            im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
            mime = "image/jpeg"
        elif fmt == "webp":
            im.save(buf, "WEBP", quality=quality, method=6)
            mime = "image/webp"
        else:  # png
            im.convert("RGBA" if im.mode in ("P", "LA") else im.mode)
            im.save(buf, "PNG", optimize=True)
            mime = "image/png"
        return buf.getvalue(), mime, (w, h)
    except Exception as err:  # noqa: BLE001 — never let one bad image break the report
        print(f"image processing failed for {path.name}: {err}", file=sys.stderr)
        return raw, mime_for(path.suffix), image_size(path)


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def build_html(
    images: list[Path], title: str, max_width: int, fmt: str, quality: int, Image
) -> str:
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    steps_html = []
    content_width = 920  # approximate rendered image width inside a .step card
    raw_total = out_total = 0
    for img in images:
        number, step_title = humanize(img.name)
        label = f"{number}. {step_title}" if number else step_title
        data, mime, dims = prepare_image(img, max_width, fmt, quality, Image)
        raw_total += img.stat().st_size
        out_total += len(data)
        if dims:
            w, h = dims
            dim_attrs = f' width="{w}" height="{h}"'
            render_h = round(content_width * h / w)
        else:
            dim_attrs = ""
            render_h = 675
        # Reserve the card's rendered height so off-screen steps (which
        # content-visibility skips) don't reflow the page when scrolled into view.
        reserve = render_h + 96  # heading + card padding
        steps_html.append(
            f"""    <section class="step" style="contain-intrinsic-size: {content_width}px {reserve}px;">
      <h2><span class="num">{html.escape(number or '')}</span>{html.escape(step_title)}</h2>
      <img alt="{html.escape(label)}"{dim_attrs} loading="lazy" decoding="async" src="{data_uri(data, mime)}" />
    </section>"""
        )
    body = "\n".join(steps_html)
    count = len(images)
    if out_total and out_total != raw_total:
        print(
            f"images: {raw_total // 1024} KB -> {out_total // 1024} KB "
            f"({fmt}, max-width {max_width}px)"
        )
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
    /* Skip painting/decoding off-screen steps so a 7-image gallery scrolls
       smoothly; the inline contain-intrinsic-size reserves each card's height. */
    content-visibility: auto;
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
    /* Force every step to render so all pages appear in the PDF. */
    .step {{ box-shadow: none; content-visibility: visible; }}
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
    parser.add_argument(
        "--max-width", type=int, default=1000,
        help="downscale screenshots wider than this many px (0 = keep original size)",
    )
    parser.add_argument(
        "--image-format", choices=("png", "webp", "jpeg", "original"), default="png",
        help="re-encode embedded images (default png; 'original' keeps source bytes)",
    )
    parser.add_argument("--quality", type=int, default=80, help="webp/jpeg quality")
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
    Image = load_pillow()
    if Image is None and args.image_format != "original":
        print(
            "Pillow not available — embedding originals at full size. Run via "
            "`uv run` to auto-install it (faster scrolling), or pass --image-format original.",
            file=sys.stderr,
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_html(images, args.title, args.max_width, args.image_format, args.quality, Image),
        encoding="utf-8",
    )
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
