"""Run a dashboard's named SQL against a connection by name, decoupled from any
session/cookie. Reads the saved connection (and its stored database) via
connect.py, queries via the driver registry; dashboard persistence lives in
dashboards.py."""

from __future__ import annotations

from typing import Any

from .connect import _connection_by_name
from .drivers import DRIVERS

# Row cap per dashboard query (matches /api/clickhouse/query's ceiling), applied
# as the LIMIT of the subselect wrapping each query.
DASHBOARD_ROW_CAP = 1000


def _parse_tsv_columns(text: str) -> dict[str, list[str]]:
    """Parse TabSeparatedWithNames into a column-oriented, insertion-ordered dict
    `{column_name: [values, …]}` (first line = names, rest = rows). Empty -> {}."""
    if text == "":
        return {}
    lines = text.split("\n")
    names = lines[0].split("\t")
    cols: dict[str, list[str]] = {name: [] for name in names}
    for line in lines[1:]:
        values = line.split("\t")
        for i, name in enumerate(names):
            cols[name].append(values[i] if i < len(values) else "")
    return cols


async def run_queries_for_connection(
    name: str, queries: dict[str, str]
) -> dict[str, Any]:
    """Run a dashboard's named queries against a saved connection by name.
    Fail-fast: an unknown connection, no selected database, or the first failing
    query aborts the call. On full success returns {"ok": True, "results": {name:
    {col: [values, …]}}} — column-oriented, ready for window.queries."""
    stored = await _connection_by_name(name)
    if stored is None:
        return {
            "ok": False,
            "reason": "no-connection",
            "message": f'no connection named "{name}"',
        }
    driver = DRIVERS[stored.type]
    if getattr(driver, "requires_database", True) and not stored.database:
        return {
            "ok": False,
            "reason": "no-database",
            "message": (
                f'connection "{name}" has no selected database — select one for it '
                "or fully-qualify table names as db.table"
            ),
        }
    results: dict[str, dict[str, list[str]]] = {}
    for qname, sql in queries.items():
        r = await driver.run_query(
            stored.config, sql, stored.database,
            limit=DASHBOARD_ROW_CAP, offset=0, order_by=None, fmt="tsv",
        )
        if not r.ok:
            return {"ok": False, "reason": "query", "message": f"{qname}: {r.value}"}
        results[qname] = _parse_tsv_columns(r.value)
    return {"ok": True, "results": results}
