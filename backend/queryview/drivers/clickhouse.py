"""ClickHouse driver: the HTTP-interface client and a Driver implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

import httpx

from .base import QueryResult, build_order_by, wrap_paginated

CH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ChConfig:
    host: str
    port: int
    username: str
    password: str


class ChResult(NamedTuple):
    ok: bool
    value: str


async def ch_query(c: ChConfig, query: str, database: str | None = None,
                   fmt: str | None = None) -> ChResult:
    """Run a query against the ClickHouse HTTP interface (Basic auth, 5s timeout).
    `database` scopes the query; `fmt` appends a ClickHouse `FORMAT` clause."""
    url = f"http://{c.host}:{c.port}/"
    q = f"{query}\nFORMAT {fmt}" if fmt else query
    params = {"query": q}
    if database:
        params["database"] = database
    try:
        async with httpx.AsyncClient(timeout=CH_TIMEOUT_SECONDS) as client:
            res = await client.get(url, params=params, auth=(c.username, c.password))
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


class ClickHouseDriver:
    type = "clickhouse"

    def parse_config(self, body: Any) -> tuple[ChConfig | None, str | None]:
        return parse_ch_config(body)

    def config_to_dict(self, config: ChConfig) -> dict[str, Any]:
        return {
            "host": config.host, "port": config.port,
            "username": config.username, "password": config.password,
        }

    def config_from_dict(self, data: dict[str, Any]) -> ChConfig:
        return ChConfig(
            host=data["host"], port=int(data["port"]),
            username=data.get("username", ""), password=data.get("password", ""),
        )

    async def test(self, config: ChConfig) -> dict[str, Any]:
        r = await ch_query(config, "SELECT 1")
        if r.ok:
            return {"ok": True, "message": f"Connected — SELECT 1 returned {r.value}"}
        return {"ok": False, "message": r.value}

    async def list_databases(self, config: ChConfig) -> tuple[bool, list[str] | str]:
        r = await ch_query(config, "SHOW DATABASES")
        if not r.ok:
            return False, r.value
        return True, [s.strip() for s in r.value.split("\n") if s.strip()]

    async def run_query(self, config: ChConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, "`")
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias=None)
        ch_fmt = "CSVWithNames" if fmt == "csv" else "TabSeparatedWithNames"
        r = await ch_query(config, paginated, database=database, fmt=ch_fmt)
        return QueryResult(r.ok, r.value)

    async def describe_query(self, config: ChConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")
        r = await ch_query(config, f"DESCRIBE (\n{inner}\n)", database=database,
                           fmt="TabSeparated")
        if not r.ok:
            return False, r.value
        fields: list[dict[str, str]] = []
        for line in r.value.split("\n"):
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            fields.append({"name": cols[0], "type": cols[1]})
        return True, fields
