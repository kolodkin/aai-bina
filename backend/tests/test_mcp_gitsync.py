"""The git-sync MCP tools: registration and delegation to gitsync (errors come
back as {ok: False} rather than raising, matching the other tools)."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_git_tools_are_registered():
    from queryview.mcp_server import mcp

    names = {t.name for t in _run(mcp.list_tools())}
    assert {"git_store", "git_history", "git_restore"} <= names


def test_git_store_tool_reports_unconfigured():
    # Outside git_env the default workspace has no remote configured.
    from queryview.mcp_server import git_store

    r = _run(git_store("query", "x", conn_type="clickhouse"))
    assert r["ok"] is False
    assert "no git remote" in r["message"]


def test_git_history_tool_reports_unconfigured():
    from queryview.mcp_server import git_history

    r = _run(git_history("dashboard", "x"))
    assert r["ok"] is False


def test_git_restore_tool_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("GIT_SYNC_REMOTE", raising=False)
    from queryview.mcp_server import git_restore

    r = _run(git_restore("dashboard", "x"))
    assert r["ok"] is False
