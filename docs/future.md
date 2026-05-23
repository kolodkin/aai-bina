# Future

Planned work and roadmap items. Each entry is a proposal, not yet implemented —
the spec lives here until it ships, then moves into the relevant doc.

## Skill: `e2e pdf`

A skill that turns the Astral e2e screenshots into a PDF artifact, scoped by a
single argument. It builds on the existing
[`e2e-report`](../.claude/skills/e2e-report/SKILL.md) skill (which already
renders all screenshots — each under its step title — to a self-contained HTML
plus a PDF via Chrome) and adds **scoping**: build the PDF for the current PR,
for one module, or for everything.

### Arguments

Invoked as `e2e pdf [<arg>]`. The argument selects what the PDF covers:

| Arg             | Default | Effect |
| --------------- | ------- | ------ |
| `pr`            | yes     | Build the e2e PDF **locally** for the current branch / PR's run — the steps that changed or were exercised on this branch. No upload; the PDF is written under `.cache/e2e-report/` and surfaced as a local file. |
| `<module name>` |         | Build the PDF for a single e2e module only (one test file / feature area), e.g. `e2e pdf connect`. |
| `all`           |         | Build one combined PDF across **all** e2e modules. |

Running `e2e pdf` with no argument is equivalent to `e2e pdf pr`.

### Behavior

- **`pr` (default)** — the common case while iterating on a branch. It uses the
  screenshots from the latest local e2e run (`scripts/setup.sh` or
  `deno task test:e2e`), renders them locally to a PDF, and hands back the file
  path. Nothing is pushed or attached to the PR by default; it is a local
  artifact for review.
- **`<module name>`** — scopes the report to the screenshots produced by the
  named module. Useful once the e2e suite is split across multiple modules so a
  reviewer can get a focused PDF for just the area under change.
- **`all`** — concatenates every module's screenshots into a single PDF, in a
  stable order, for a full-suite snapshot.

### Notes & open questions

- **Module mapping.** This assumes the e2e suite grows beyond the current single
  `e2e/tests/app.test.ts` into per-module test files, with screenshots namespaced
  by module (e.g. `screenshots/<module>/NN-step.png`). The current
  `NN-step-name.png` naming already drives ordering and titles; module scoping
  would key off the subdirectory or a filename prefix.
- **`pr` selection.** "What this PR exercised" needs a concrete definition —
  options include the full local run on the branch, or only modules whose source
  changed vs. the base branch. Start with the full local run; refine later.
- **CI hook (optional).** The same skill could run in CI after the e2e step and
  upload the PDF as a workflow artifact, mirroring the existing HTML e2e report.
- **Reuse.** The rendering (titles from file names, HTML + Chrome-to-PDF) is
  already implemented in `e2e-report`; this skill is mostly the argument parsing
  and screenshot selection on top of it.

## Related docs

- [api.md](./api.md) — backend JSON API.
- [connect.md](./connect.md) — connecting, storage, sessions.
- [queryview.md](./queryview.md) — the single-prompt page concept.
