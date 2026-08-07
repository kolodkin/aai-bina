# Package pytest + e2e gates for the PyPI publish flow

## Goal

Gate the `aaibina` PyPI release on the backend pytest suite and the Playwright
e2e suite running against the built wheel — aaiclick-style — and adopt
aaiclick's side-by-side test layout so the backend tests ship inside the
package.

## Test layout

Pytest tests sit side by side with the code they test, inside the relevant
module package. E2e (Playwright) tests keep their own top-level `e2e/` folder.

Moves from `backend/tests/` (files unchanged unless noted):

| From | To |
| --- | --- |
| `conftest.py` | `backend/queryview/conftest.py` |
| `test_remote.py`, `test_migrations.py`, `test_validation.py`, `test_dashboards.py`, `test_workspaces.py`, `test_queries.py`, `test_gitsync.py`, `test_mcp_gitsync.py`, `test_connect_flow.py`, `test_connect_store.py`, `test_api_workspaces.py`, `test_api_gitsync.py`, `test_api_db.py` | `backend/queryview/<same name>` |
| `test_drivers_base.py` | `backend/queryview/drivers/test_base.py` |
| `test_driver_contract.py` | `backend/queryview/drivers/test_contract.py` |
| `test_driver_clickhouse.py` | `backend/queryview/drivers/test_clickhouse.py` |
| `test_driver_duckdb.py` | `backend/queryview/drivers/test_duckdb.py` |
| `test_driver_postgres.py` | `backend/queryview/drivers/test_postgres.py` |

No new `__init__.py` files: tests land inside existing packages, so hatch
(`packages = ["backend/queryview"]`) ships them in the wheel automatically.
The suite is already wheel-clean: tests import only `queryview.*`, alembic
config and migrations resolve from inside the package, and `conftest.py`
redirects the SQLite store to a per-session tempdir.

## Workflow changes

### `.github/workflows/ci.yml`

`test-backend` runs `uv run --frozen pytest backend/queryview` (path change
only).

### `.github/workflows/publish.yaml`

- `build` job additionally exports pinned test dependencies and uploads them:

  ```yaml
  uv export --frozen --group test --no-emit-project -o requirements-test.txt
  ```

- New `test-package-pytest` job (`needs: build`, **no checkout** — it tests
  the installed package, not the repo):
  1. Setup uv, `uv venv --python 3.11`.
  2. Download `dist` and `requirements` artifacts.
  3. `uv pip install -r requirements-test.txt`
  4. `uv pip install --no-deps --no-index --find-links dist/ aaibina`
  5. From `${{ runner.temp }}`: `uv run --no-project pytest --pyargs queryview`
     (with `VIRTUAL_ENV` pointing at the workspace venv).
- New `test-package-e2e` job (`needs: [build, test-package-pytest]` — the
  expensive e2e run starts only after the backend pytests pass). A copy of
  ci.yml's `test-e2e` job with the app-under-test swapped from source to the
  built wheel:
  - Same ClickHouse + Postgres `services:` blocks, checkout (for `e2e/` and
    the `start-git-daemon` composite action), Playwright chromium install,
    git daemon, and pytest invocation.
  - No Node/npm/frontend-build steps — the wheel already contains the SPA.
  - Deps: `uv sync --frozen --group test --no-install-project`, then install
    the wheel from the `dist` artifact; start the server with the installed
    `aaibina` binary so Playwright drives the exact bytes being published.
  - No report-deploy / checks-annotation steps (they need `DEPLOY_KEY`);
    upload Playwright traces as an artifact on failure instead.
  - Skippable via a new `skip-e2e` boolean `workflow_dispatch` input
    (emergency escape hatch for flake-blocked releases, mirroring aaiclick's
    `skip-docker-e2e`).
- `publish` job: `needs: [test-package, test-package-pytest, test-package-e2e]`
  with an aaiclick-style `if: always() && ...` condition requiring smoke and
  pytest success and e2e success-or-skipped. The curl smoke test stays (it
  verifies the bundled SPA serves even if e2e is skipped).

## Docs

- `README.md` publish section: mention the release is also gated on the
  packaged test suite and the Playwright e2e suite run against the installed
  wheel.
- `CLAUDE.md`: new `# Tests` section stating the convention — tests side by
  side with code in the relevant module package; e2e tests in their own
  top-level folder; the packaged suite must pass against the installed wheel
  (`pytest --pyargs queryview`).

## Out of scope

No container images. No test matrix. No e2e report publishing from the release
workflow.
