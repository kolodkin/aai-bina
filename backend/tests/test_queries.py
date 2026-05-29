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
