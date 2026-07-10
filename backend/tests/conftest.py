"""Shared fixtures for backend (non-e2e) tests.

Redirects the SQLite store and encryption-key file to a per-session tempdir so
tests don't touch the real `backend/queryview.db`, and resets the lazy
module-level engine/schema state in `queryview.connect` before tests run."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """A local bare repo as the git-sync remote + a fresh clone dir, via env vars."""
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


@pytest.fixture(scope="session", autouse=True)
def _isolated_db(tmp_path_factory: pytest.TempPathFactory):
    tmp: Path = tmp_path_factory.mktemp("qv_backend_tests")
    os.environ["DB_PATH"] = str(tmp / "test.db")
    os.environ["DB_KEY_PATH"] = str(tmp / "test.db.key")

    # The workspaces migration seeds the default workspace from GIT_SYNC_*;
    # tests control that per-test (monkeypatch), never from ambient env.
    os.environ.pop("GIT_SYNC_REMOTE", None)
    os.environ.pop("GIT_SYNC_BRANCH", None)

    # Reset the lazy globals so the next DB touch picks up the new paths.
    import queryview.connect as _c

    _c._engine = None  # type: ignore[attr-defined]
    _c._schema_ready = False  # type: ignore[attr-defined]
    _c._key = None  # type: ignore[attr-defined]
    yield
