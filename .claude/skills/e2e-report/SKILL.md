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

The generator is pure Python stdlib — no dependencies to install.

```bash
python .claude/skills/e2e-report/build_report.py
```

It auto-discovers the screenshots directory (first match of `$SCREENSHOT_DIR`,
`.cache/screenshots`, `e2e/screenshots`, `./screenshots`) and writes
`.cache/e2e-report/index.html` (plus `index.pdf` if Chrome is found).

Common options:

- `--screenshots DIR` — point at a specific screenshots directory.
- `--out PATH` — output HTML path; the PDF is written alongside with a `.pdf`
  suffix.
- `--title "..."` — report heading (default `QueryView E2E Report`).
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
