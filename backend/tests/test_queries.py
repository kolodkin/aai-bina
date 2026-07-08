"""Tests for the predefined-queries store: cell_view round-trips through save/list."""

from __future__ import annotations

import asyncio

from queryview.queries import list_predefined_queries, save_predefined_query


def _run(coro):
    return asyncio.run(coro)


def test_save_and_list_round_trips_cell_view():
    _run(
        save_predefined_query(
            "cves",
            "clickhouse",
            "SELECT cve_id FROM t",
            cell_view="cve_id:\n  type: link\n  value: https://nvd.nist.gov/vuln/detail/{cell}\n",
        )
    )
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(r for r in rows if r["query_name"] == "cves")
    assert row["query"] == "SELECT cve_id FROM t"
    assert "nvd.nist.gov" in row["cell_view"]
    assert "{cell}" in row["cell_view"]


def test_save_without_cell_view_lists_as_none():
    _run(save_predefined_query("plain", "clickhouse", "SELECT 1"))
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(r for r in rows if r["query_name"] == "plain")
    assert row["cell_view"] is None


def test_upsert_overwrites_cell_view():
    _run(save_predefined_query("u", "clickhouse", "SELECT 1", cell_view="a: {type: link, value: x}"))
    _run(save_predefined_query("u", "clickhouse", "SELECT 1", cell_view="b: {type: link, value: y}"))
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(r for r in rows if r["query_name"] == "u")
    assert "b:" in row["cell_view"]
    assert "a:" not in row["cell_view"]


def test_clearing_cell_view_persists_null():
    _run(save_predefined_query("c", "clickhouse", "SELECT 1", cell_view="x: {type: link, value: y}"))
    _run(save_predefined_query("c", "clickhouse", "SELECT 1", cell_view=None))
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(r for r in rows if r["query_name"] == "c")
    assert row["cell_view"] is None


def test_order_by_and_fields_round_trip():
    _run(
        save_predefined_query(
            "q1",
            "clickhouse",
            "SELECT 1",
            cell_view=None,
            order_by='[{"name":"id","dir":"DESC"}]',
            fields='["id","name"]',
        )
    )
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(r for r in rows if r["query_name"] == "q1")
    assert row["order_by"] == '[{"name":"id","dir":"DESC"}]'
    assert row["fields"] == '["id","name"]'


def test_null_presentation_is_preserved():
    _run(save_predefined_query("q2", "clickhouse", "SELECT 2"))
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(r for r in rows if r["query_name"] == "q2")
    assert row["order_by"] is None and row["fields"] is None


def test_mcp_list_queries_parses_presentation():
    from queryview.mcp_server import list_queries

    _run(
        save_predefined_query(
            "lq", "clickhouse", "SELECT 1",
            order_by='[{"name":"id","dir":"ASC"}]',
            fields='["id"]',
        )
    )
    out = _run(list_queries("clickhouse"))
    row = next(r for r in out["queries"] if r["query_name"] == "lq")
    assert row["order_by"] == [{"name": "id", "dir": "ASC"}]
    assert row["fields"] == ["id"]
    assert row["query"] == "SELECT 1"


def test_columns_to_rows():
    from queryview.mcp_server import _columns_to_rows

    out = _columns_to_rows({"a": ["1", "2"], "b": ["x", "y"]})
    assert out == {"columns": ["a", "b"], "rows": [["1", "x"], ["2", "y"]]}


def test_columns_to_rows_empty():
    from queryview.mcp_server import _columns_to_rows

    assert _columns_to_rows({}) == {"columns": [], "rows": []}
