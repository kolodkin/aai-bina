"""DuckDB driver: file-based, no network, no picker. The synchronous duckdb
library is driven in a worker thread so the event loop is never blocked."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

import duckdb

from .base import QueryResult, build_order_by, serialize_rows, wrap_paginated


@dataclass(frozen=True)
class DuckConfig:
    path: str


def parse_duck_config(body: Any) -> tuple[DuckConfig | None, str | None]:
    b = body if isinstance(body, dict) else {}
    raw = b.get("path")
    path = raw.strip() if isinstance(raw, str) else ""
    return DuckConfig(path=path or ":memory:"), None


def _open(path: str):
    # read_only avoids lock contention between concurrent describe/query opens;
    # :memory: cannot be read_only, so open it read-write.
    return duckdb.connect(path, read_only=(path != ":memory:"))


class DuckDBDriver:
    type: str = "duckdb"
    requires_database: bool = False

    def parse_config(self, body: Any) -> tuple[DuckConfig | None, str | None]:
        return parse_duck_config(body)

    def config_to_dict(self, config: DuckConfig) -> dict[str, Any]:
        return asdict(config)

    def config_from_dict(self, data: dict[str, Any]) -> DuckConfig:
        return DuckConfig(**data)

    async def test(self, config: DuckConfig) -> dict[str, Any]:
        def _work():
            con = _open(config.path)
            try:
                return con.execute("SELECT 1").fetchone()[0]
            finally:
                con.close()
        try:
            val = await asyncio.to_thread(_work)
            return {"ok": True, "message": f"Connected — SELECT 1 returned {val}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e) or "connection failed"}

    async def list_databases(self, config: DuckConfig) -> tuple[bool, list[str] | str]:
        # No picker: queries run directly against the file (schema-qualify in SQL).
        return True, []

    async def run_query(self, config: DuckConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, '"')
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias="_qv")

        def _work():
            con = _open(config.path)
            try:
                cur = con.execute(paginated)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                return columns, rows
            finally:
                con.close()
        try:
            columns, rows = await asyncio.to_thread(_work)
            return QueryResult(True, serialize_rows(columns, rows, fmt))
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e))

    async def describe_query(self, config: DuckConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")

        def _work():
            con = _open(config.path)
            try:
                # DuckDB's DESCRIBE returns (column_name, column_type, ...).
                return con.execute(f"DESCRIBE {inner}").fetchall()
            finally:
                con.close()
        try:
            rows = await asyncio.to_thread(_work)
            return True, [{"name": r[0], "type": r[1]} for r in rows]
        except Exception as e:  # noqa: BLE001
            return False, str(e)
