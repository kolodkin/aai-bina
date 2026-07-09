"""Git sync: serializer round-trips (queries/dashboards <-> repo files) and,
in later tests, store/history/restore against a local bare repo (no network)."""

from __future__ import annotations

import asyncio
import subprocess

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


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """A local bare repo as the remote + a fresh clone dir, via env vars."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("GIT_SYNC_REMOTE", str(remote))
    monkeypatch.setenv("GIT_SYNC_DIR", str(tmp_path / "clone"))
    monkeypatch.delenv("GIT_SYNC_BRANCH", raising=False)
    return remote


def _remote_log(remote) -> str:
    return subprocess.run(
        ["git", "log", "--format=%H %s", "main"],
        cwd=remote,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_store_unconfigured_is_409(monkeypatch):
    monkeypatch.delenv("GIT_SYNC_REMOTE", raising=False)
    with pytest.raises(GitSyncError) as e:
        _run(gitsync.store("query", "anything", "clickhouse"))
    assert e.value.status == 409
    assert "not configured" in str(e.value)


def test_store_missing_entity_is_404(git_env):
    with pytest.raises(GitSyncError) as e:
        _run(gitsync.store("query", "no such query xyz", "clickhouse"))
    assert e.value.status == 404


def test_store_query_commits_and_pushes(git_env):
    from queryview.queries import save_predefined_query

    _run(save_predefined_query("gs top", "clickhouse", "SELECT 1"))
    r = _run(gitsync.store("query", "gs top", "clickhouse"))
    assert r["committed"] is True and r["sha"]
    log = _remote_log(git_env)
    assert r["sha"] in log
    assert "store query clickhouse/gs top" in log


def test_store_no_change_makes_no_commit(git_env):
    from queryview.queries import save_predefined_query

    _run(save_predefined_query("gs same", "clickhouse", "SELECT 2"))
    r1 = _run(gitsync.store("query", "gs same", "clickhouse"))
    r2 = _run(gitsync.store("query", "gs same", "clickhouse"))
    assert r1["committed"] is True
    assert r2 == {"committed": False, "sha": None, "message": "no changes"}
    assert _remote_log(git_env).count("\n") == 1  # exactly one commit


def test_store_dashboard_touches_only_its_dir(git_env):
    from queryview.dashboards import upsert_dashboard

    _run(upsert_dashboard("gs dash", "prod", "<html>v1</html>", {"q": "SELECT 1"}))
    r = _run(gitsync.store("dashboard", "gs dash"))
    assert r["committed"] is True
    out = subprocess.run(
        ["git", "show", "--name-only", "--format=", r["sha"]],
        cwd=git_env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    files = [line for line in out.splitlines() if line.strip()]
    assert files and all(f.startswith("dashboards/gs dash/") for f in files)
    assert set(files) == {
        "dashboards/gs dash/meta.yaml",
        "dashboards/gs dash/dashboard.html",
        "dashboards/gs dash/queries.yaml",
    }


def test_store_custom_message(git_env):
    from queryview.queries import save_predefined_query

    _run(save_predefined_query("gs msg", "clickhouse", "SELECT 3"))
    _run(gitsync.store("query", "gs msg", "clickhouse", message="before migration"))
    assert "before migration" in _remote_log(git_env)


def test_store_unreachable_remote_raises_without_init(tmp_path, monkeypatch):
    from queryview.queries import save_predefined_query

    monkeypatch.setenv("GIT_SYNC_REMOTE", str(tmp_path / "does-not-exist.git"))
    monkeypatch.setenv("GIT_SYNC_DIR", str(tmp_path / "clone"))
    _run(save_predefined_query("gs unreachable", "clickhouse", "SELECT 1"))
    with pytest.raises(GitSyncError) as e:
        _run(gitsync.store("query", "gs unreachable", "clickhouse"))
    assert e.value.status == 502
    assert not (tmp_path / "clone" / ".git").exists()  # no spurious local repo


def _clone_head() -> str:
    import os

    wd = os.environ["GIT_SYNC_DIR"]
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _store_query_versions(name: str, sqls: list[str]) -> list[str]:
    """Save+store the query once per SQL; returns the commit shas in order."""
    from queryview.queries import save_predefined_query

    shas = []
    for sql in sqls:
        _run(save_predefined_query(name, "clickhouse", sql))
        shas.append(_run(gitsync.store("query", name, "clickhouse"))["sha"])
    return shas


def test_history_pages_newest_first(git_env):
    shas = _store_query_versions("gs hist", ["SELECT 1", "SELECT 2", "SELECT 3"])
    page1 = _run(gitsync.history("query", "gs hist", "clickhouse", limit=2))
    assert [r["sha"] for r in page1["revisions"]] == [shas[2], shas[1]]
    assert page1["has_more"] is True
    assert all(isinstance(r["date"], int) and r["message"] for r in page1["revisions"])
    page2 = _run(
        gitsync.history("query", "gs hist", "clickhouse", before=shas[1], limit=2)
    )
    assert [r["sha"] for r in page2["revisions"]] == [shas[0]]
    assert page2["has_more"] is False
    # Paging past the oldest commit yields an empty page, not an error.
    page3 = _run(
        gitsync.history("query", "gs hist", "clickhouse", before=shas[0], limit=2)
    )
    assert page3 == {"revisions": [], "has_more": False}


def test_history_only_sees_own_entity(git_env):
    _store_query_versions("gs mine", ["SELECT 1"])
    _store_query_versions("gs other", ["SELECT 9"])
    h = _run(gitsync.history("query", "gs mine", "clickhouse"))
    assert len(h["revisions"]) == 1


def test_restore_old_version_overwrites_db_without_moving_head(git_env):
    from queryview.queries import list_predefined_queries

    shas = _store_query_versions("gs restore", ["SELECT 1", "SELECT 2"])
    head_before = _clone_head()
    r = _run(gitsync.restore("query", "gs restore", "clickhouse", ref=shas[0]))
    assert r["restored"] is True
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(x for x in rows if x["query_name"] == "gs restore")
    assert row["query"] == "SELECT 1"
    assert _clone_head() == head_before  # HEAD never moves


def test_restore_default_ref_is_remote_head(git_env):
    from queryview.queries import list_predefined_queries, save_predefined_query

    _store_query_versions("gs latest", ["SELECT 1", "SELECT 2"])
    _run(save_predefined_query("gs latest", "clickhouse", "SELECT 999"))  # local drift
    _run(gitsync.restore("query", "gs latest", "clickhouse"))
    rows = _run(list_predefined_queries("clickhouse"))
    row = next(x for x in rows if x["query_name"] == "gs latest")
    assert row["query"] == "SELECT 2"


def test_restore_dashboard_round_trip(git_env):
    from queryview.dashboards import get_dashboard, upsert_dashboard

    _run(upsert_dashboard("gs rdash", "prod", "<html>v1</html>", {"q": "SELECT 1"}))
    _run(gitsync.store("dashboard", "gs rdash"))
    _run(upsert_dashboard("gs rdash", "other", "<html>v2</html>", {"q": "SELECT 2"}))
    _run(gitsync.restore("dashboard", "gs rdash"))
    d = _run(get_dashboard("gs rdash"))
    assert d == {
        "name": "gs rdash",
        "connection": "prod",
        "html": "<html>v1</html>",
        "queries": {"q": "SELECT 1"},
    }


def test_restore_missing_at_ref_is_404(git_env):
    _store_query_versions("gs exists", ["SELECT 1"])
    with pytest.raises(GitSyncError) as e:
        _run(gitsync.restore("query", "never stored", "clickhouse"))
    assert e.value.status == 404
