"""Postgres driver (asyncpg). Short-lived connections per request; the picker
lists real databases and the selected one is where queries run."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from .base import QueryResult, build_order_by, serialize_rows, wrap_paginated

PG_TIMEOUT_SECONDS = 5.0
_BOOTSTRAP_DBS = ("postgres", "template1")


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    username: str
    password: str


def parse_pg_config(body: Any) -> tuple[PgConfig | None, str | None]:
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
    return PgConfig(host=host, port=port, username=username, password=password), None


async def _connect(c: PgConfig, database: str | None):
    return await asyncpg.connect(
        host=c.host, port=c.port,
        user=c.username or None, password=c.password or None,
        database=database, timeout=PG_TIMEOUT_SECONDS, command_timeout=PG_TIMEOUT_SECONDS,
    )


async def _connect_bootstrap(c: PgConfig):
    """Connect to *some* database so we can enumerate the rest."""
    candidates = ([c.username] if c.username else []) + list(_BOOTSTRAP_DBS)
    last: Exception | None = None
    for db in candidates:
        try:
            return await _connect(c, db)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last if last is not None else RuntimeError("could not connect")


class PostgresDriver:
    type = "postgres"
    requires_database = True

    def parse_config(self, body: Any) -> tuple[PgConfig | None, str | None]:
        return parse_pg_config(body)

    def config_to_dict(self, config: PgConfig) -> dict[str, Any]:
        return {
            "host": config.host, "port": config.port,
            "username": config.username, "password": config.password,
        }

    def config_from_dict(self, data: dict[str, Any]) -> PgConfig:
        return PgConfig(
            host=data["host"], port=int(data["port"]),
            username=data.get("username", ""), password=data.get("password", ""),
        )

    async def test(self, config: PgConfig) -> dict[str, Any]:
        try:
            conn = await _connect_bootstrap(config)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e) or "connection failed"}
        try:
            val = await conn.fetchval("SELECT 1")
            return {"ok": True, "message": f"Connected — SELECT 1 returned {val}"}
        finally:
            await conn.close()

    async def list_databases(self, config: PgConfig) -> tuple[bool, list[str] | str]:
        try:
            conn = await _connect_bootstrap(config)
        except Exception as e:  # noqa: BLE001
            return False, str(e) or "connection failed"
        try:
            rows = await conn.fetch(
                "SELECT datname FROM pg_database "
                "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
            )
            return True, [r["datname"] for r in rows]
        finally:
            await conn.close()

    async def run_query(self, config: PgConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, '"')
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias="_qv")
        try:
            conn = await _connect(config, database)
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e) or "connection failed")
        try:
            stmt = await conn.prepare(paginated)
            columns = [a.name for a in stmt.get_attributes()]
            records = await stmt.fetch()
            return QueryResult(True, serialize_rows(columns, [list(r) for r in records], fmt))
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e))
        finally:
            await conn.close()

    async def describe_query(self, config: PgConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")
        try:
            conn = await _connect(config, database)
        except Exception as e:  # noqa: BLE001
            return False, str(e) or "connection failed"
        try:
            stmt = await conn.prepare(inner)
            return True, [{"name": a.name, "type": a.type.name} for a in stmt.get_attributes()]
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        finally:
            await conn.close()
