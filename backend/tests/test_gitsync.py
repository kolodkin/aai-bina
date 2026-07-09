"""Git sync: serializer round-trips (queries/dashboards <-> repo files) and,
in later tests, store/history/restore against a local bare repo (no network)."""

from __future__ import annotations

import asyncio

import pytest

from queryview import gitsync
from queryview.gitsync import (
    GitSyncError,
    dashboard_from_files,
    dashboard_reldir,
    dashboard_to_files,
    query_from_yaml,
    query_relpath,
    query_to_yaml,
    slug,
)


def _run(coro):
    return asyncio.run(coro)


def test_slug_passthrough_and_encoding():
    assert slug("top_errors") == "top_errors"
    assert slug("top errors 2.0") == "top errors 2.0"
    assert slug("a/b") == "a%2Fb"
    assert slug(".hidden") == "%2Ehidden"
    assert slug("héllo") == "h%C3%A9llo"


def test_relpaths():
    assert query_relpath("clickhouse", "top errors") == "queries/clickhouse/top errors.yaml"
    assert dashboard_reldir("sales/eu") == "dashboards/sales%2Feu"


def test_query_yaml_round_trip():
    row = {
        "query_name": "top errors",
        "query": "SELECT *\nFROM errors\nORDER BY n DESC",
        "cell_view": "cve_id:\n  type: link\n  value: https://x/{cell}\n",
        "order_by": '[{"name": "n", "dir": "DESC"}]',
        "fields": '["cve_id", "n"]',
    }
    text = query_to_yaml(row)
    assert "SELECT *" in text  # multiline SQL is a readable block, not \n escapes
    assert query_from_yaml(text) == row


def test_query_yaml_omits_and_restores_none_fields():
    row = {
        "query_name": "plain",
        "query": "SELECT 1",
        "cell_view": None,
        "order_by": None,
        "fields": None,
    }
    text = query_to_yaml(row)
    assert "cell_view" not in text
    assert query_from_yaml(text) == row


def test_query_from_yaml_rejects_malformed():
    with pytest.raises(GitSyncError):
        query_from_yaml("just a scalar")
    with pytest.raises(GitSyncError):
        query_from_yaml("query_name: x\n")  # no query


def test_dashboard_files_round_trip():
    d = {
        "name": "sales",
        "connection": "prod",
        "html": "<html>\n<body>hi — ünicode</body>\n</html>",
        "queries": {"revenue": "SELECT 1", "multi": "SELECT a\nFROM b"},
    }
    files = dashboard_to_files(d)
    assert set(files) == {"meta.yaml", "dashboard.html", "queries.yaml"}
    assert files["dashboard.html"] == d["html"]  # verbatim
    assert dashboard_from_files(files) == d


def test_dashboard_from_files_rejects_malformed_meta():
    with pytest.raises(GitSyncError):
        dashboard_from_files(
            {"meta.yaml": "connection: x", "dashboard.html": "", "queries.yaml": ""}
        )
