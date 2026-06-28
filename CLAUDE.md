# Commit Guideline

1. Avoid `Co-Authored-By: Claude` (and similar Claude/AI attribution) trailers in
   commit messages and PR bodies.

# Operational Guidelines

1. Use `uv run` (e.g. `uv run pytest`, `uv run python ...`), not `python -m` or a
   manually activated virtualenv.

# Conventions

## Python

- **Avoid `__all__`.** Don't declare `__all__` in modules or packages. Keep the
  public surface implicit: import a name where it's defined (e.g.
  `from queryview.drivers.base import DriverConfig`) rather than re-exporting it
  through a package `__init__` and listing it in `__all__`. A package `__init__`
  may still import names it genuinely uses (e.g. building a registry), but it
  should not maintain an explicit export list.
