# Commit Guideline

1. Avoid `Co-Authored-By: Claude` (and similar Claude/AI attribution) trailers in
   commit messages and PR bodies.

# Operational Guidelines

1. Use `uv run` (e.g. `uv run pytest`, `uv run python ...`), not `python -m` or a
   manually activated virtualenv.

# Conventions

## Docs

- **Remove superpowers plans and designs once implemented.** Files under
  `docs/superpowers/plans/` and `docs/superpowers/specs/` exist only while the
  work is pending; when a plan ships, delete its plan and spec in the same
  change (the feature's page under `docs/` is the doc of record) and fix any
  links that pointed at them.

## Python

- **Avoid `__all__`.** Don't declare `__all__` in modules or packages. Keep the
  public surface implicit: import a name where it's defined (e.g.
  `from queryview.drivers.base import DriverConfig`) rather than re-exporting it
  through a package `__init__` and listing it in `__all__`. A package `__init__`
  may still import names it genuinely uses (e.g. building a registry), but it
  should not maintain an explicit export list.
