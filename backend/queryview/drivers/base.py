"""The driver contract (Protocol) plus dialect helpers and the row serializer
shared by row-returning drivers. No backend/storage concerns here."""
from __future__ import annotations

import csv
import io
from typing import Any, NamedTuple, Protocol, runtime_checkable


class QueryResult(NamedTuple):
    ok: bool
    value: str  # serialized rows when ok; an error message otherwise


@runtime_checkable
class Driver(Protocol):
    type: str
    # Whether queries require a database to be selected first (a non-empty
    # picker). False for file-based drivers like DuckDB that have no picker.
    requires_database: bool

    def parse_config(self, body: Any) -> tuple[Any | None, str | None]: ...
    def config_to_dict(self, config: Any) -> dict[str, Any]: ...
    def config_from_dict(self, data: dict[str, Any]) -> Any: ...
    async def test(self, config: Any) -> dict[str, Any]: ...
    async def list_databases(self, config: Any) -> tuple[bool, list[str] | str]: ...
    async def run_query(
        self, config: Any, sql: str, database: str | None,
        limit: int, offset: int, order_by: list[dict[str, Any]] | None, fmt: str,
    ) -> QueryResult: ...
    async def describe_query(
        self, config: Any, sql: str, database: str | None,
    ) -> tuple[bool, list[dict[str, str]] | str]: ...


def _parse_port(raw: Any) -> int | None:
    """Coerce a raw port value to an int in [1, 65535], or None. Rejects bool
    (a bool is an int subclass) and out-of-range / non-numeric values."""
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def parse_host_port_config(body: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the host/port/username/password fields shared by network drivers
    (ClickHouse, Postgres). Returns ({host,port,username,password}, None) or
    (None, message)."""
    b = body if isinstance(body, dict) else {}
    raw_host = b.get("host")
    host = raw_host.strip() if isinstance(raw_host, str) else ""
    port = _parse_port(b.get("port"))
    username = b.get("username") if isinstance(b.get("username"), str) else ""
    password = b.get("password") if isinstance(b.get("password"), str) else ""
    if not host:
        return None, "host required"
    if port is None:
        return None, "valid port required"
    return {"host": host, "port": port, "username": username, "password": password}, None


def build_order_by(order_by: list[dict[str, Any]] | None, quote: str) -> str:
    """`ORDER BY` clause from `[{"name","dir"}]`. Names are `quote`-quoted (any
    embedded quote doubled) and directions whitelisted to ASC/DESC, so malformed
    input can't inject SQL. Empty/absent input yields no clause."""
    if not order_by:
        return ""
    parts: list[str] = []
    for col in order_by:
        if not isinstance(col, dict):
            continue
        name = col.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_dir = col.get("dir")
        direction = raw_dir.upper() if isinstance(raw_dir, str) else ""
        if direction not in ("ASC", "DESC"):
            direction = "ASC"
        escaped = name.replace(quote, quote + quote)
        parts.append(f"{quote}{escaped}{quote} {direction}")
    if not parts:
        return ""
    return "ORDER BY " + ", ".join(parts)


def wrap_paginated(
    sql: str, order_clause: str, limit: int, offset: int, alias: str | None = None,
) -> str:
    """Wrap a SELECT in a paginating subselect. `alias` (e.g. `_qv`) is required
    by Postgres/DuckDB for a derived table; ClickHouse passes alias=None to keep
    its historical SQL byte-for-byte identical."""
    inner = sql.rstrip().rstrip(";")
    head = f"SELECT * FROM (\n{inner}\n)"
    if alias:
        head += f" AS {alias}"
    clauses = [head]
    if order_clause:
        clauses.append(order_clause)
    clauses.append(f"LIMIT {int(limit)} OFFSET {int(offset)}")
    return " ".join(clauses)


def serialize_rows(columns: list[str], rows: list[Any], fmt: str) -> str:
    """Serialize rows to the text contract ClickHouse emits: TabSeparatedWithNames
    (fmt='tsv') or CSVWithNames (fmt='csv'). None -> empty field. Non-strings are
    str()-ified. No trailing newline (matches ClickHouse's stripped output)."""
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if v is None else str(v) for v in row])
        return buf.getvalue().rstrip("\n")
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)
