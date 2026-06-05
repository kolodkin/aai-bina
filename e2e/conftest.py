import os
import re
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

# Mirror playwright.config.ts: 15s default assertion timeout.
expect.set_options(timeout=15_000)


@pytest.fixture(scope="session")
def base_url() -> str:
    # The app under test is started separately (Vite dev server, or the FastAPI
    # backend serving the built SPA); point at it with BASE_URL.
    return os.environ.get("BASE_URL", "http://localhost:5173")


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    return {**browser_type_launch_args, "args": ["--no-sandbox"]}


@pytest.fixture
def browser_context_args(browser_context_args: dict) -> dict:
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}}


@pytest.fixture
def shot(page: Page, output_path: str):
    """Save labeled, ordered screenshots into pytest-playwright's per-test
    output directory; files are numbered so an e2e screenshot report can
    preserve call order when bundling them into a self-contained HTML.
    """
    out = Path(output_path)
    counter = {"n": 0}

    def _shot(label: str) -> None:
        counter["n"] += 1
        safe = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "shot"
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / f"{counter['n']:02d}-{safe}.png"), full_page=True)

    return _shot


# --- ClickHouse seeding for query tests -----------------------------------
# Coordinates default to the connection-form defaults the suite uses.
CH_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CH_PORT = os.environ.get("CLICKHOUSE_PORT", "8123")
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")


def _ch_exec(sql: str) -> None:
    """Run a statement against ClickHouse over HTTP. POST allows writes (the GET
    interface is read-only by default), so seeding/teardown go through here."""
    res = httpx.post(
        f"http://{CH_HOST}:{CH_PORT}/",
        content=sql.encode("utf-8"),
        auth=(CH_USER, CH_PASSWORD),
        timeout=10.0,
    )
    res.raise_for_status()


@pytest.fixture(scope="module")
def seeded_test_db():
    """Module-level: create a ClickHouse database named `test` with a small
    `items` table of known rows, then drop the whole database on teardown.

    Drops `test` up front too, so seeding is idempotent and never accumulates
    rows from an earlier run that left the database behind."""
    _ch_exec("DROP DATABASE IF EXISTS test")
    _ch_exec("CREATE DATABASE test")
    _ch_exec(
        "CREATE TABLE test.items (id UInt32, name String) ENGINE = MergeTree ORDER BY id"
    )
    _ch_exec(
        "INSERT INTO test.items (id, name) VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')"
    )
    yield
    _ch_exec("DROP DATABASE IF EXISTS test")
