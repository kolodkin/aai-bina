---
name: e2e-report
description: Build a single consolidated e2e artifact from the Astral browser screenshots — every screenshot shown under its humanized step title, as a self-contained HTML file plus a PDF (when Chrome is available). Use when the user asks for an "e2e report", "e2e artifact", a document/gallery of the e2e screenshots, or wants to bundle the screenshots from .cache/screenshots (or SCREENSHOT_DIR) into one shareable file.
---

# e2e-report

Turns the e2e screenshots into one shareable artifact: each step's screenshot
under its title (derived from the screenshot file name, matching the step names
the test prints), as a self-contained HTML file with images embedded, plus a
PDF rendered through Chrome when one is available.

## When to use

- The user wants a "nice e2e artifact", an e2e screenshot report, or a
  document/gallery of the run.
- After an e2e run has produced screenshots (locally via `scripts/setup.sh` or
  `deno task test:e2e`; in CI they land under the blob report).

## How to run

Run with `uv run` so Pillow (declared in the script's PEP 723 metadata) is
auto-installed — it downscales the screenshots, which is what keeps the gallery
scrolling smoothly:

```bash
uv run .claude/skills/e2e-report/build_report.py
```

Plain `python .claude/skills/e2e-report/build_report.py` also works but, without
Pillow, embeds the screenshots at full resolution (heavier to scroll).

It auto-discovers the screenshots directory (first match of `$SCREENSHOT_DIR`,
`.cache/screenshots`, `e2e/screenshots`, `./screenshots`) and writes
`.cache/e2e-report/index.html` (plus `index.pdf` if Chrome is found).

Common options:

- `--screenshots DIR` — point at a specific screenshots directory.
- `--out PATH` — output HTML path; the PDF is written alongside with a `.pdf`
  suffix.
- `--title "..."` — report heading (default `QueryView E2E Report`).
- `--max-width PX` — downscale screenshots wider than this (default `1000`;
  `0` keeps original size). Smaller = smoother scrolling.
- `--image-format png|webp|jpeg|original` — re-encode embedded images (default
  `png`; `webp`/`jpeg` shrink bytes further; `original` keeps source bytes).
- `--quality N` — webp/jpeg quality (default `80`).
- `--pdf` — require a PDF (exit non-zero if no Chrome); `--no-pdf` — HTML only.
- `CHROME_PATH=/path/to/chrome` — use a specific Chrome/Chromium for the PDF.

## Notes

- Titles come from the file name: `05-selecting-a-database-shows-the-connected-indicator.png`
  becomes step **5. "Selecting a database shows the connected indicator"**. Keep
  e2e screenshots named `NN-step-description.png` (the suite already does this)
  so ordering and titles stay correct.
- The HTML is self-contained (base64 images), so it travels as a single file.
- Screenshot output dirs (`.cache/`, `e2e/.../screenshots`) are gitignored;
  surface the generated artifact with the SendUserFile tool rather than
  committing it.
- To wire it into CI, run this script after the e2e step and upload
  `.cache/e2e-report/` (HTML+PDF) as a workflow artifact.
