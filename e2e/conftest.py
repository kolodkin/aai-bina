import os
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

# Mirror playwright.config.ts: 15s default assertion timeout.
expect.set_options(timeout=15_000)

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _isolated_backend(db_path: Path) -> Iterator[str]:
    """Start our own backend on a throwaway DB_PATH so e2e never touches the
    operational backend/queryview.db (the store the dev server uses). Serves the
    built SPA via SERVE_STATIC; builds it first if there's no dist yet."""
    if not (REPO / "frontend" / "dist" / "index.html").exists():
        print("e2e: no frontend/dist — building the SPA (npm run build)…")
        subprocess.run(["npm", "run", "build", "-w", "frontend"], cwd=REPO, check=True)
    else:
        print("e2e: reusing existing frontend/dist (rebuild it to test UI changes)")

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "SERVE_STATIC": "1", "PORT": str(port), "DB_PATH": str(db_path)}
    proc = subprocess.Popen(
        ["uv", "run", "queryview-backend"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                raise RuntimeError(f"e2e backend exited early (code {proc.returncode}):\n{out}")
            try:
                httpx.get(f"{url}/api/health", timeout=1.0)
                break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise RuntimeError("e2e backend never became healthy on /api/health")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    # When BASE_URL is set (CI starts a server with its own ephemeral DB_PATH),
    # use it as-is. Otherwise start an isolated backend on a temp DB so a local
    # e2e run never clobbers the operational connection store.
    external = os.environ.get("BASE_URL")
    if external:
        yield external
        return
    db_path = tmp_path_factory.mktemp("qv_e2e_db") / "qv.db"
    yield from _isolated_backend(db_path)


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


# --- Postgres seeding for query tests -------------------------------------
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")


@pytest.fixture(scope="module")
def seeded_pg_db():
    """Create a `qvtest` database with a small `items` table; drop it after.
    Uses asyncpg (a project dependency). The async work runs in a worker thread
    because pytest-playwright's sync API keeps an event loop on the main thread,
    so asyncio.run() can't be called there directly."""
    import asyncio
    import concurrent.futures

    import asyncpg

    async def _seed():
        sys = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD or None, database="postgres",
        )
        await sys.execute("DROP DATABASE IF EXISTS qvtest WITH (FORCE)")
        await sys.execute("CREATE DATABASE qvtest")
        await sys.close()
        db = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD or None, database="qvtest",
        )
        await db.execute("CREATE TABLE items (id int, name text)")
        await db.execute(
            "INSERT INTO items (id, name) VALUES (1,'alpha'),(2,'beta'),(3,'gamma')"
        )
        await db.close()

    async def _teardown():
        sys = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD or None, database="postgres",
        )
        await sys.execute("DROP DATABASE IF EXISTS qvtest WITH (FORCE)")
        await sys.close()

    def _in_thread(make_coro):
        # A fresh thread has no running loop, so asyncio.run works there.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(make_coro())).result()

    _in_thread(_seed)
    yield
    _in_thread(_teardown)


# --- DuckDB seeding for query tests ---------------------------------------
@pytest.fixture(scope="module")
def seeded_duckdb(tmp_path_factory) -> str:
    """A temp DuckDB file with a small `items` table; returns its path for the
    connection form to point at."""
    import duckdb

    path = tmp_path_factory.mktemp("duck") / "qv.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    con.execute("INSERT INTO items VALUES (1,'alpha'),(2,'beta'),(3,'gamma')")
    con.close()
    return str(path)
