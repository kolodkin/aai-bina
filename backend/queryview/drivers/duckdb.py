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
    ident_quote: str = '"'

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

    async def list_tables(self, config: DuckConfig,
                          database: str | None) -> tuple[bool, list[dict[str, Any]] | str]:
        # SHOW TABLES for the name set, joined with duckdb_tables()'s estimated
        # row count (absent for views). Bytes are distinct storage blocks ×
        # block size from pragma_storage_info — metadata only, no data scan;
        # None for views and whenever the pragma has nothing (e.g. :memory:).
        def _work():
            con = _open(config.path)
            try:
                names = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
                est = dict(
                    con.execute(
                        "SELECT table_name, estimated_size FROM duckdb_tables()"
                    ).fetchall()
                )
                block_size = con.execute(
                    "SELECT block_size FROM pragma_database_size()"
                ).fetchone()[0]

                def _bytes(name: str) -> int | None:
                    if name not in est:  # a view — no storage of its own
                        return None
                    quoted = name.replace("'", "''")
                    try:
                        blocks = con.execute(
                            "SELECT count(DISTINCT block_id) "
                            f"FROM pragma_storage_info('{quoted}') WHERE block_id >= 0"
                        ).fetchone()[0]
                    except Exception:  # noqa: BLE001
                        return None
                    return blocks * block_size if blocks else None

                return [
                    {"name": n, "rows": est.get(n), "bytes": _bytes(n)} for n in names
                ]
            finally:
                con.close()
        try:
            return True, await asyncio.to_thread(_work)
        except Exception as e:  # noqa: BLE001
            return False, str(e)

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
