"""Running a dashboard's named SQL against a connection by name, decoupled from
any session/cookie. Reads the saved connection (and its stored database) via
connect.py and talks to ClickHouse via clickhouse.py; the persistence of
dashboards themselves lives in dashboards.py."""

from __future__ import annotations

from typing import Any

from .clickhouse import ch_query
from .connect import _connection_by_name

# A dashboard query's result is capped at this many rows, matching the 1000-row
# ceiling on /api/clickhouse/query. Applied as the LIMIT of the paginating
# subselect that wraps each query.
DASHBOARD_ROW_CAP = 1000


def _parse_tsv_columns(text: str) -> dict[str, list[str]]:
    """Parse TabSeparatedWithNames into a column-oriented, insertion-ordered dict
    `{column_name: [values, …]}`. The first line is the column names; the rest
    are rows. Empty output yields an empty dict."""
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
    """Run a dashboard's named queries against a saved connection by name,
    decoupled from any session/cookie. Fail-fast: an unknown connection, a
    connection with no selected database, or the first failing query aborts the
    whole call. On full success returns {"ok": True, "results": {name: {col:
    [values, …]}}} — column-oriented dicts ready for window.queries."""
    stored = await _connection_by_name(name)
    if stored is None:
        return {
            "ok": False,
            "reason": "no-connection",
            "message": f'no connection named "{name}"',
        }
    if not stored.database:
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
        inner = sql.rstrip().rstrip(";")
        paginated = f"SELECT * FROM (\n{inner}\n) LIMIT {DASHBOARD_ROW_CAP}"
        r = await ch_query(
            stored.config, paginated, database=stored.database, fmt="TabSeparatedWithNames"
        )
        if not r.ok:
            return {"ok": False, "reason": "query", "message": f"{qname}: {r.value}"}
        results[qname] = _parse_tsv_columns(r.value)
    return {"ok": True, "results": results}
