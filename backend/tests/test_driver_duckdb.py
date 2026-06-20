"""DuckDB driver against a real temp-file database: config round-trip, no
picker, paginated query, describe, and registry conformance."""
from __future__ import annotations

import asyncio

import duckdb
import pytest

from queryview.drivers import DRIVERS, Driver
from queryview.drivers.duckdb import DuckConfig, DuckDBDriver


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def duck_path(tmp_path):
    path = tmp_path / "qv.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    con.execute("INSERT INTO items VALUES (1,'alpha'),(2,'beta'),(3,'gamma')")
    con.close()
    return str(path)


def test_registry_has_duckdb_satisfying_protocol():
    d = DRIVERS["duckdb"]
    assert isinstance(d, Driver)
    assert d.type == "duckdb" and d.requires_database is False


def test_parse_config_defaults_blank_path_to_memory():
    d = DuckDBDriver()
    assert d.parse_config({"path": ""})[0] == DuckConfig(":memory:")
    assert d.parse_config({"path": "/tmp/x.duckdb"})[0] == DuckConfig("/tmp/x.duckdb")


def test_config_dict_round_trip():
    d = DuckDBDriver()
    assert d.config_from_dict(d.config_to_dict(DuckConfig("/p"))) == DuckConfig("/p")


def test_list_databases_is_empty(duck_path):
    d = DuckDBDriver()
    assert _run(d.list_databases(DuckConfig(duck_path))) == (True, [])


def test_run_query_paginates_and_serializes(duck_path):
    d = DuckDBDriver()
    r = _run(d.run_query(DuckConfig(duck_path), "SELECT id, name FROM items ORDER BY id",
                         None, 2, 0, [{"name": "name", "dir": "ASC"}], "tsv"))
    assert r.ok
    assert r.value == "id\tname\n1\talpha\n2\tbeta"


def test_describe_query_returns_columns(duck_path):
    d = DuckDBDriver()
    ok, fields = _run(d.describe_query(DuckConfig(duck_path), "SELECT id, name FROM items", None))
    assert ok
    names = [f["name"] for f in fields]
    assert names == ["id", "name"]


def test_run_query_error_is_reported(duck_path):
    d = DuckDBDriver()
    r = _run(d.run_query(DuckConfig(duck_path), "SELECT * FROM no_such", None, 10, 0, None, "tsv"))
    assert r.ok is False and "no_such" in r.value
