"""ClickHouse driver: how to talk to ClickHouse and the shape of a connection
config. No HTTP-server or storage concerns live here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

import httpx

CH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ChConfig:
    host: str
    port: int
    username: str
    password: str


class ChResult(NamedTuple):
    ok: bool
    # The query text when ok; an error message otherwise.
    value: str


async def ch_query(c: ChConfig, query: str) -> ChResult:
    """Run a query against the ClickHouse HTTP interface (Basic auth, 5s timeout)."""
    url = f"http://{c.host}:{c.port}/"
    try:
        async with httpx.AsyncClient(timeout=CH_TIMEOUT_SECONDS) as client:
            res = await client.get(
                url, params={"query": query}, auth=(c.username, c.password)
            )
    except httpx.TimeoutException:
        return ChResult(False, "connection timed out")
    except httpx.HTTPError as err:
        return ChResult(False, str(err) or "connection failed")

    text = res.text.strip()
    if not res.is_success:
        return ChResult(False, f"ClickHouse responded {res.status_code}: {text[:200]}")
    return ChResult(True, text)


def parse_ch_config(body: Any) -> tuple[ChConfig | None, str | None]:
    """Validate a ClickHouse config from a request body. Returns (config, None) or
    (None, message)."""
    b = body if isinstance(body, dict) else {}

    raw_host = b.get("host")
    host = raw_host.strip() if isinstance(raw_host, str) else ""

    raw_port = b.get("port")
    port: int | None
    if isinstance(raw_port, bool):
        port = None
    elif isinstance(raw_port, int):
        port = raw_port
    elif isinstance(raw_port, str):
        try:
            port = int(raw_port)
        except ValueError:
            port = None
    else:
        port = None

    username = b.get("username") if isinstance(b.get("username"), str) else ""
    password = b.get("password") if isinstance(b.get("password"), str) else ""

    if not host:
        return None, "host required"
    if port is None or port <= 0 or port > 65535:
        return None, "valid port required"
    return ChConfig(host=host, port=port, username=username, password=password), None


async def test_connection(c: ChConfig) -> dict[str, Any]:
    """Test only: a throwaway connectivity check."""
    r = await ch_query(c, "SELECT 1")
    if r.ok:
        return {"ok": True, "message": f"Connected — SELECT 1 returned {r.value}"}
    return {"ok": False, "message": r.value}


async def list_databases(c: ChConfig) -> tuple[bool, list[str] | str]:
    """List the connection's databases. Returns (True, databases) or (False, message)."""
    r = await ch_query(c, "SHOW DATABASES")
    if not r.ok:
        return False, r.value
    databases = [s.strip() for s in r.value.split("\n") if s.strip()]
    return True, databases
