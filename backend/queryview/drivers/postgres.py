"""Postgres driver (asyncpg). Short-lived connections per request; the picker
lists real databases and the selected one is where queries run."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg

from .base import (
    QueryResult,
    build_order_by,
    parse_host_port_config,
    serialize_rows,
    wrap_paginated,
)

PG_TIMEOUT_SECONDS = 5.0
_BOOTSTRAP_DBS = ("postgres", "template1")


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    username: str
    password: str


def parse_pg_config(body: Any) -> tuple[PgConfig | None, str | None]:
    fields, err = parse_host_port_config(body)
    if err:
        return None, err
    return PgConfig(**fields), None


async def _raw_connect(c: PgConfig, database: str | None):
    return await asyncpg.connect(
        host=c.host, port=c.port,
        user=c.username or None, password=c.password or None,
        database=database, timeout=PG_TIMEOUT_SECONDS, command_timeout=PG_TIMEOUT_SECONDS,
    )


async def _raw_connect_bootstrap(c: PgConfig):
    """Connect to *some* database so we can enumerate the rest."""
    candidates = ([c.username] if c.username else []) + list(_BOOTSTRAP_DBS)
    last: Exception | None = None
    for db in candidates:
        try:
            return await _raw_connect(c, db)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last if last is not None else RuntimeError("could not connect")


@asynccontextmanager
async def _connect(c: PgConfig, database: str | None):
    """Short-lived connection to a specific database, closed on exit."""
    conn = await _raw_connect(c, database)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def _connect_bootstrap(c: PgConfig):
    """Short-lived connection to a maintenance database, closed on exit."""
    conn = await _raw_connect_bootstrap(c)
    try:
        yield conn
    finally:
        await conn.close()


class PostgresDriver:
    type = "postgres"
    requires_database = True

    def parse_config(self, body: Any) -> tuple[PgConfig | None, str | None]:
        return parse_pg_config(body)

    def config_to_dict(self, config: PgConfig) -> dict[str, Any]:
        return asdict(config)

    def config_from_dict(self, data: dict[str, Any]) -> PgConfig:
        return PgConfig(**data)

    async def test(self, config: PgConfig) -> dict[str, Any]:
        try:
            async with _connect_bootstrap(config) as conn:
                val = await conn.fetchval("SELECT 1")
                return {"ok": True, "message": f"Connected — SELECT 1 returned {val}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e) or "connection failed"}

    async def list_databases(self, config: PgConfig) -> tuple[bool, list[str] | str]:
        try:
            async with _connect_bootstrap(config) as conn:
                rows = await conn.fetch(
                    "SELECT datname FROM pg_database "
                    "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
                )
                return True, [r["datname"] for r in rows]
        except Exception as e:  # noqa: BLE001
            return False, str(e) or "connection failed"

    async def run_query(self, config: PgConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, '"')
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias="_qv")
        try:
            async with _connect(config, database) as conn:
                stmt = await conn.prepare(paginated)
                columns = [a.name for a in stmt.get_attributes()]
                records = await stmt.fetch()
                return QueryResult(True, serialize_rows(columns, [list(r) for r in records], fmt))
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e) or "connection failed")

    async def describe_query(self, config: PgConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")
        try:
            async with _connect(config, database) as conn:
                stmt = await conn.prepare(inner)
                return True, [
                    {"name": a.name, "type": a.type.name} for a in stmt.get_attributes()
                ]
        except Exception as e:  # noqa: BLE001
            return False, str(e) or "connection failed"
