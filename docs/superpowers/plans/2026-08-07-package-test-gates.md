# Package pytest + e2e release gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the `aaibina` PyPI release on the backend pytest suite and the Playwright e2e suite, both running against the built wheel, with backend tests moved side by side with the code so they ship inside the package.

**Architecture:** Backend tests move from `backend/tests/` into the `queryview` package (aaiclick's layout), so hatch ships them in the wheel and the release can run `pytest --pyargs queryview` with no checkout. The publish workflow gains two sequential gates after `build`: a checkout-free `test-package-pytest` job, then a `test-package-e2e` job that reuses ci.yml's service containers but drives the installed wheel instead of the source tree.

**Tech Stack:** GitHub Actions, uv (lockfile-pinned installs), pytest, pytest-playwright, hatchling/hatch-vcs.

**Spec:** `docs/superpowers/specs/2026-08-07-package-pytest-gate-design.md`

## Global Constraints

- Work on branch `claude/aaibina-pypi-publish-flow-1e1n0f` in `/home/user/aai-bina`; push with `git push -u origin claude/aaibina-pypi-publish-flow-1e1n0f`.
- No `Co-Authored-By: Claude` (or similar AI attribution) trailers in commit messages (aai-bina CLAUDE.md).
- Use `uv run` for all Python invocations, never `python -m` or an activated venv.
- Do NOT run the publish workflow itself — it publishes to PyPI for real. Validate via YAML parsing, the moved tests passing locally, and a dispatched CI run.
- All test files move unchanged (content byte-identical); only paths and, for driver tests, filenames change.

---

### Task 1: Move backend tests into the package, update CI path

**Files:**
- Move: `backend/tests/*.py` → `backend/queryview/` and `backend/queryview/drivers/` (mapping below)
- Modify: `.github/workflows/ci.yml:45` (test path)

**Interfaces:**
- Produces: packaged test modules importable as `queryview.test_*` and `queryview.drivers.test_*`; Tasks 2–3 rely on `pytest --pyargs queryview` collecting them from an installed wheel.

- [ ] **Step 1: Move the files with git mv**

```bash
cd /home/user/aai-bina
git mv backend/tests/conftest.py backend/queryview/conftest.py
for f in test_remote test_migrations test_validation test_dashboards \
         test_workspaces test_queries test_gitsync test_mcp_gitsync \
         test_connect_flow test_connect_store test_api_workspaces \
         test_api_gitsync test_api_db; do
  git mv "backend/tests/$f.py" "backend/queryview/$f.py"
done
git mv backend/tests/test_drivers_base.py      backend/queryview/drivers/test_base.py
git mv backend/tests/test_driver_contract.py   backend/queryview/drivers/test_contract.py
git mv backend/tests/test_driver_clickhouse.py backend/queryview/drivers/test_clickhouse.py
git mv backend/tests/test_driver_duckdb.py     backend/queryview/drivers/test_duckdb.py
git mv backend/tests/test_driver_postgres.py   backend/queryview/drivers/test_postgres.py
```

Then confirm `backend/tests/` is gone: `ls backend/tests 2>&1` → "No such file or directory". Do NOT add any `__init__.py` — the targets are already packages.

- [ ] **Step 2: Update the module docstring path reference in conftest.py**

`backend/queryview/conftest.py`'s docstring mentions "the real `backend/queryview.db`" — leave that (it's a DB path, not the tests path). Grep for stale references to the old location:

```bash
grep -rn "backend/tests" . --include="*.py" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.json" --include="*.toml" | grep -v node_modules
```

Expected: only `.github/workflows/ci.yml` (fixed next step) and possibly `docs/superpowers/` (spec/plan themselves — ignore). Fix any other hit by updating the path to `backend/queryview`.

- [ ] **Step 3: Update ci.yml test path**

In `.github/workflows/ci.yml`, `test-backend` job, change:

```yaml
      - name: Run backend unit tests
        run: uv run --frozen pytest backend/tests
```

to:

```yaml
      - name: Run backend unit tests
        run: uv run --frozen pytest backend/queryview
```

- [ ] **Step 4: Run the moved suite locally**

```bash
uv sync --frozen --group test
uv run --frozen pytest backend/queryview
```

Expected: same pass/skip counts as before the move, zero errors. If collection errors mention duplicate module names or import mismatches, stop and diagnose (superpowers:systematic-debugging) — do not add `__init__.py` files or rename without understanding.

- [ ] **Step 5: Run lint/format hooks**

```bash
uv sync --frozen --group dev
uv run --frozen --group dev pre-commit run --all-files
```

Expected: PASS (ruff may re-sort imports in moved files; if hooks modify files, re-run until clean and include the fixes).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Move backend tests side by side with code inside the queryview package"
```

---

### Task 2: Publish workflow — packaged pytest gate

**Files:**
- Modify: `.github/workflows/publish.yaml` (build job ~line 66; new job after `test-package`; `publish` needs ~line 112)

**Interfaces:**
- Consumes: packaged tests from Task 1 (`pytest --pyargs queryview`).
- Produces: `requirements` artifact (`requirements-test.txt`) and job id `test-package-pytest`; Task 3 wires `needs` on it.

- [ ] **Step 1: Export pinned test deps in the build job**

In `publish.yaml`, after the "Upload dist artifacts" step of the `build` job, add:

```yaml
      - name: Export pinned test dependencies
        run: uv export --frozen --group test --no-emit-project -o requirements-test.txt

      - name: Upload requirements artifact
        uses: actions/upload-artifact@v4
        with:
          name: requirements
          path: requirements-test.txt
```

- [ ] **Step 2: Add the test-package-pytest job**

After the existing `test-package` job, add:

```yaml
  # Run the packaged backend test suite against the installed wheel. No
  # checkout — the tests ship inside the queryview package, so this exercises
  # exactly the bits users install.
  test-package-pytest:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - name: Setup uv
        uses: astral-sh/setup-uv@v5

      - name: Create virtual environment
        run: uv venv --python 3.11

      - name: Download dist artifacts
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Download requirements artifact
        uses: actions/download-artifact@v4

      - name: Install pinned dependencies and built wheel
        run: |
          uv pip install -r requirements-test.txt
          uv pip install --no-deps --no-index --find-links dist/ aaibina

      - name: Run packaged tests against the installed wheel
        working-directory: ${{ runner.temp }}
        env:
          VIRTUAL_ENV: ${{ github.workspace }}/.venv
        run: uv run --no-project pytest --pyargs queryview
```

Note: the requirements download step has no `path:` — the artifact lands in the workspace root where the next step reads it (same pattern as aaiclick).

- [ ] **Step 3: Gate publish on the new job**

Change the `publish` job's `needs: test-package` to:

```yaml
    needs: [test-package, test-package-pytest]
```

- [ ] **Step 4: Validate YAML**

```bash
uv run --frozen python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/publish.yaml').read_text()); print('ok')"
```

Expected: `ok`. (pyyaml is a runtime dependency, so it's importable.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/publish.yaml
git commit -m "Gate PyPI publish on the packaged pytest suite run against the wheel"
```

---

### Task 3: Publish workflow — Playwright e2e gate

**Files:**
- Modify: `.github/workflows/publish.yaml` (`workflow_dispatch` inputs ~line 8; new job after `test-package-pytest`; `publish` needs/if)

**Interfaces:**
- Consumes: `dist` artifact from `build`; job id `test-package-pytest` from Task 2; ci.yml's `services:` blocks (copy source, lines 53–77) and `.github/actions/start-git-daemon`.
- Produces: job id `test-package-e2e` and `skip-e2e` input; the `publish` gate condition below is final.

- [ ] **Step 1: Add the skip-e2e input**

Under `on.workflow_dispatch.inputs`, after `pre-release`, add:

```yaml
      skip-e2e:
        description: "Skip the Playwright e2e release gate (emergency only)"
        required: false
        type: boolean
        default: false
```

- [ ] **Step 2: Add the test-package-e2e job**

After `test-package-pytest`, add. The `services:` blocks are copied verbatim from ci.yml's `test-e2e` job (clickhouse + postgres, including health checks); the steps differ from ci.yml only where noted:

```yaml
  # Playwright e2e against the installed wheel: server and SPA are the exact
  # bytes being published (no Node/frontend build — the wheel ships the SPA).
  # The checkout supplies only e2e/, uv.lock, and the git-daemon action.
  # Runs after the packaged pytest gate so the expensive leg starts only once
  # the cheap one passes.
  test-package-e2e:
    needs: [build, test-package-pytest]
    if: ${{ !inputs.skip-e2e }}
    runs-on: ubuntu-latest
    services:
      clickhouse:
        image: clickhouse/clickhouse-server:24
        ports:
          - 8123:8123
        # Probe the HTTP interface (8123) — what the backend actually uses —
        # so the readiness gate matches the app.
        options: >-
          --health-cmd "wget --spider -q localhost:8123/ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
          --add-host=host.docker.internal:host-gateway
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_HOST_AUTH_METHOD: trust
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4

      - name: Setup uv
        uses: astral-sh/setup-uv@v5

      - name: Install test dependencies (project not installed)
        run: uv sync --frozen --group test --no-install-project

      - name: Download dist artifacts
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Install built wheel
        run: uv pip install --no-deps dist/aaibina-*.whl

      - name: Install Playwright browser
        run: uv run --no-sync playwright install --with-deps chromium

      - name: Start git daemon
        uses: ./.github/actions/start-git-daemon

      - name: Start backend from the installed wheel
        run: |
          SERVE_STATIC=1 .venv/bin/aaibina > backend.log 2>&1 &
          echo $! > backend.pid
          for i in $(seq 1 30); do
            if curl -sf http://localhost:8000/api/health > /dev/null; then
              echo "Backend is up"; exit 0
            fi
            sleep 1
          done
          echo "Backend failed to start"; cat backend.log; exit 1

      - name: Run Playwright e2e tests
        env:
          BASE_URL: http://localhost:8000
        run: |
          uv run --no-sync pytest e2e \
            --tracing retain-on-failure \
            --output test-results

      - name: Stop backend
        if: always()
        run: |
          if [ -f backend.pid ]; then
            kill "$(cat backend.pid)" 2>/dev/null || true
          fi

      - name: Upload trace artifacts
        if: ${{ !cancelled() }}
        uses: actions/upload-artifact@v4
        with:
          name: playwright-traces-release
          path: test-results/
          retention-days: 1
          if-no-files-found: ignore
```

Key deviations from ci.yml (do not "fix" them back): `uv sync --no-install-project` + wheel install instead of plain `uv sync` (the source package must NOT be installed, or it would shadow the wheel); `uv run --no-sync` everywhere after (plain `uv run --frozen` would re-sync and install the source package over the wheel); server starts via `.venv/bin/aaibina` (the wheel's entry point), not `uv run queryview-backend`; no Node/npm/frontend-build; no report deploy/check steps.

- [ ] **Step 3: Final publish gate condition**

Replace the `publish` job's `needs:` line (from Task 2) with:

```yaml
    needs: [test-package, test-package-pytest, test-package-e2e]
    if: >-
      always()
      && needs.test-package.result == 'success'
      && needs.test-package-pytest.result == 'success'
      && (needs.test-package-e2e.result == 'success'
          || needs.test-package-e2e.result == 'skipped')
```

(`always()` is required: with `skip-e2e` the e2e job's result is `skipped`, which would otherwise skip `publish` too.)

- [ ] **Step 4: Validate YAML**

```bash
uv run --frozen python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/publish.yaml').read_text()); print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/publish.yaml
git commit -m "Gate PyPI publish on Playwright e2e against the installed wheel"
```

---

### Task 4: Docs — README publish section and CLAUDE.md Tests convention

**Files:**
- Modify: `README.md` (~lines 89–96, the "Publish to PyPI" paragraph)
- Modify: `CLAUDE.md` (new `# Tests` section)

**Interfaces:**
- Consumes: gate names from Tasks 2–3 (packaged pytest suite, Playwright e2e, `skip-e2e` input).

- [ ] **Step 1: Update the README publish paragraph**

Read `README.md` lines 85–100 first. The current paragraph says the workflow "smoke-tests the installed package, publishes `aaibina` via PyPI trusted publishing". Rewrite the release-gates part so it reads (keep surrounding sentences and link formatting intact):

```markdown
manual dispatch) builds the SPA into the wheel (`queryview/static/`), then
gates the release on the installed wheel: an HTTP smoke test, the packaged
backend test suite (`pytest --pyargs queryview`), and the Playwright e2e
suite driving the packaged server (skippable via the `skip-e2e` input for
emergencies). It then publishes
[`aaibina`](https://pypi.org/project/aaibina/) via PyPI trusted publishing,
```

Adjust line wrapping to match the file's style; keep the rest of the paragraph (tagging, GitHub release) unchanged.

- [ ] **Step 2: Add the Tests section to CLAUDE.md**

In `CLAUDE.md`, after `# Operational Guidelines` and before `# Conventions`, add:

```markdown
# Tests

1. Pytest tests live side by side with the code they test, inside the relevant
   module package (e.g. `backend/queryview/test_queries.py`,
   `backend/queryview/drivers/test_clickhouse.py`), with shared fixtures in
   `backend/queryview/conftest.py`. They ship in the wheel, and the release
   workflow runs them against the installed package
   (`pytest --pyargs queryview`).
2. Exception: e2e (Playwright) tests live in the top-level `e2e/` folder —
   they test the running app through its HTTP surface, not a module.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document packaged test layout and release gates"
```

---

### Task 5: Validate on CI, clean up spec/plan, push

**Files:**
- Delete: `docs/superpowers/specs/2026-08-07-package-pytest-gate-design.md`
- Delete: `docs/superpowers/plans/2026-08-07-package-test-gates.md`

- [ ] **Step 1: Push the branch**

```bash
git push -u origin claude/aaibina-pypi-publish-flow-1e1n0f
```

(Retry up to 4 times with 2s/4s/8s/16s backoff only on network errors.)

- [ ] **Step 2: Dispatch CI on the branch and watch it**

ci.yml has `workflow_dispatch`. Trigger it for ref `claude/aaibina-pypi-publish-flow-1e1n0f` via the GitHub MCP `actions_run_trigger` tool (or the devpowers:action-run skill), then poll `actions_get`/job logs until completion. Expected: `lint`, `test-backend` (now running `backend/queryview`), and `test-e2e` all green. If `test-backend` fails on collection/imports, debug with superpowers:systematic-debugging — likely causes: a test module name colliding with a real module, or a missing file in the move.

Note: the publish workflow itself must NOT be dispatched (it publishes for real). Its changes are validated by YAML parsing (Tasks 2–3) and by the next real release.

- [ ] **Step 3: Delete the spec and plan (they've shipped)**

```bash
git rm docs/superpowers/specs/2026-08-07-package-pytest-gate-design.md
git rm docs/superpowers/plans/2026-08-07-package-test-gates.md
git commit -m "Remove shipped superpowers spec and plan for the package test gates"
git push
```

(aai-bina CLAUDE.md: plans/specs exist only while work is pending.)

- [ ] **Step 4: Verify clean state**

```bash
git status --short   # expect empty
git log --oneline origin/main..HEAD
```

Expected: the move, two workflow commits, docs commit, spec/plan lifecycle commits — all pushed.
