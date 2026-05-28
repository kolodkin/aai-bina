"""Tests for the predefined-queries store: cell_view round-trips, and the
ALTER-TABLE migration that adds cell_view to pre-existing tables."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from queryview.connect import _db_path
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


def test_migration_adds_cell_view_to_preexisting_table(tmp_path: Path, monkeypatch):
    """A SQLite file that already has predefined_queries without cell_view gets
    the column added on first use; the ALTER is idempotent on a second call."""
    db = tmp_path / "legacy.db"
    # Build the old schema by hand: predefined_queries without cell_view.
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE predefined_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_name TEXT NOT NULL,
            type TEXT NOT NULL,
            query TEXT NOT NULL,
            UNIQUE (type, query_name)
        )
        """
    )
    con.execute(
        "INSERT INTO predefined_queries (query_name, type, query) VALUES (?, ?, ?)",
        ("legacy", "clickhouse", "SELECT 1"),
    )
    con.commit()
    con.close()

    monkeypatch.setenv("DB_PATH", str(db))
    # Reset lazy globals so the next DB touch reopens at the new path.
    import queryview.connect as _c
    import queryview.queries as _q

    monkeypatch.setattr(_c, "_engine", None, raising=False)
    monkeypatch.setattr(_c, "_schema_ready", False, raising=False)
    monkeypatch.setattr(_q, "_cell_view_migrated", False, raising=False)

    # First touch runs the migration.
    rows = _run(list_predefined_queries("clickhouse"))
    legacy = next(r for r in rows if r["query_name"] == "legacy")
    assert legacy["cell_view"] is None  # migrated column reads back NULL

    # Writing a cell_view through the API now works.
    _run(save_predefined_query("legacy", "clickhouse", "SELECT 1", cell_view="x: {type: link, value: y}"))
    rows = _run(list_predefined_queries("clickhouse"))
    legacy = next(r for r in rows if r["query_name"] == "legacy")
    assert legacy["cell_view"] is not None

    # A second call must be a no-op (idempotent) — running schema again doesn't fail.
    monkeypatch.setattr(_c, "_schema_ready", False, raising=False)
    monkeypatch.setattr(_q, "_cell_view_migrated", False, raising=False)
    _run(list_predefined_queries("clickhouse"))  # would raise if ALTER ran twice non-idempotently
    assert _db_path() == db
