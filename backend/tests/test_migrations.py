"""Alembic owns the schema: a fresh DB must be migrated to head (all three
tables present and stamped in alembic_version), not built by create_all."""

from __future__ import annotations

import asyncio
import os
import sqlite3

from queryview.connect import _ensure_schema


def _run(coro):
    return asyncio.run(coro)


def test_fresh_db_is_migrated_to_head():
    _run(_ensure_schema())

    con = sqlite3.connect(os.environ["DB_PATH"])
    try:
        names = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # Alembic ran (not create_all): the version table exists alongside the
        # three application tables.
        assert {
            "connections",
            "predefined_queries",
            "dashboards",
            "alembic_version",
        } <= names, f"missing tables, got {sorted(names)}"

        versions = [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
    finally:
        con.close()

    assert len(versions) == 1 and versions[0], versions

    # The stamped revision is the latest in the migration tree.
    from alembic.script import ScriptDirectory

    from queryview.connect import _alembic_config

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    assert versions[0] == head, f"stamped {versions[0]} != head {head}"
