"""Connection domain: the SQLModel/SQLite connection store (passwords encrypted
at rest) and per-session active connections. No HTTP-server concerns live here;
the operations return plain results and main.py maps them to responses."""

from __future__ import annotations

import base64
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from .clickhouse import ChConfig, list_databases

# --- Storage (SQLite, lazily opened) --------------------------------------


class Connection(SQLModel, table=True):
    __tablename__ = "connections"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    host: str
    port: int
    username: str
    password: str  # encrypted at rest, never plaintext
    database: str | None = Field(default=None)
    last_active_at: int  # unix ms; the max is the "latest active"


def _db_path() -> Path:
    env = os.environ.get("DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "queryview.db"


_engine = None
_schema_ready = False


def _engine_for_db():
    """The async SQLAlchemy engine (aiosqlite), memoized. Lazy so importing this
    module has no side effects — no file is touched until the first query."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(f"sqlite+aiosqlite:///{_db_path()}")
    return _engine


async def _ensure_schema() -> None:
    """Create the tables on first DB use (idempotent)."""
    global _schema_ready
    if _schema_ready:
        return
    async with _engine_for_db().begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    _schema_ready = True


# --- Password encryption at rest (AES-256-GCM) ----------------------------
# The key comes from DB_ENCRYPTION_KEY (base64, 32 bytes) or a generated local
# key file next to the DB (gitignored). Stored values are base64(iv ‖ ciphertext),
# where AES-GCM appends its 16-byte tag to the ciphertext.


def _key_path() -> Path:
    env = os.environ.get("DB_KEY_PATH")
    return Path(env) if env else Path(f"{_db_path()}.key")


_key: bytes | None = None


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("DB_ENCRYPTION_KEY")
    if env_key:
        return base64.b64decode(env_key)
    path = _key_path()
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raw = os.urandom(32)
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        return raw


def _key_bytes() -> bytes:
    global _key
    if _key is None:
        _key = _load_or_create_key()
    return _key


def _encrypt_password(plain: str) -> str:
    iv = os.urandom(12)
    ct = AESGCM(_key_bytes()).encrypt(iv, plain.encode("utf-8"), None)
    return base64.b64encode(iv + ct).decode("ascii")


def _decrypt_password(stored: str) -> str:
    combined = base64.b64decode(stored)
    iv, ct = combined[:12], combined[12:]
    return AESGCM(_key_bytes()).decrypt(iv, ct, None).decode("utf-8")


@dataclass
class StoredConnection:
    name: str
    config: ChConfig
    database: str | None


async def _save_active_connection(name: str, c: ChConfig) -> None:
    password = _encrypt_password(c.password)
    now = _now_ms()
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Connection).where(Connection.name == name))).first()
        if row is None:
            row = Connection(
                name=name,
                host=c.host,
                port=c.port,
                username=c.username,
                password=password,
                last_active_at=now,
            )
        else:
            # Upsert by name; the selected database is intentionally left as-is.
            row.host = c.host
            row.port = c.port
            row.username = c.username
            row.password = password
            row.last_active_at = now
        s.add(row)
        await s.commit()


async def _save_selected_database(name: str, database: str) -> None:
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Connection).where(Connection.name == name))).first()
        if row is not None:
            row.database = database
            s.add(row)
            await s.commit()


def _row_to_stored(row: Connection | None) -> StoredConnection | None:
    if row is None:
        return None
    try:
        password = _decrypt_password(row.password)
    except Exception:
        # Unreadable (key changed / legacy plaintext) — treat as unavailable.
        return None
    return StoredConnection(
        name=row.name,
        config=ChConfig(
            host=row.host, port=row.port, username=row.username, password=password
        ),
        database=row.database,
    )


async def _latest_active_connection() -> StoredConnection | None:
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (
            await s.exec(
                select(Connection).order_by(Connection.last_active_at.desc()).limit(1)
            )
        ).first()
        return _row_to_stored(row)


async def _connection_by_name(name: str) -> StoredConnection | None:
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Connection).where(Connection.name == name))).first()
        return _row_to_stored(row)


async def _touch_connection(name: str) -> None:
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Connection).where(Connection.name == name))).first()
        if row is not None:
            row.last_active_at = _now_ms()
            s.add(row)
            await s.commit()


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- Sessions (one active connection per session, keyed by a cookie) ------


@dataclass
class _SessionState:
    name: str
    config: ChConfig
    databases: list[str]
    database: str | None


# Cap the session map and evict the least-recently-used entry so it can't grow
# unbounded (every fresh cookie adds one). An evicted session transparently
# rebuilds on its next request via _ensure_session. OrderedDict keeps insertion
# order, so re-inserting on access moves an entry to the most-recently-used end.
_sessions: "OrderedDict[str, _SessionState]" = OrderedDict()
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "1000"))


def _get_session_entry(sid: str) -> _SessionState | None:
    s = _sessions.get(sid)
    if s is not None:
        _sessions.move_to_end(sid)
    return s


def _set_session_entry(sid: str, state: _SessionState) -> None:
    _sessions[sid] = state
    _sessions.move_to_end(sid)
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)


async def _build_session(
    name: str, config: ChConfig, database: str | None
) -> tuple[_SessionState | None, str | None]:
    """List a connection's databases and build a session object."""
    ok, result = await list_databases(config)
    if not ok:
        return None, result  # type: ignore[return-value]
    databases: list[str] = result  # type: ignore[assignment]
    return (
        _SessionState(
            name=name,
            config=config,
            databases=databases,
            database=database if database and database in databases else None,
        ),
        None,
    )


async def _ensure_session(sid: str) -> None:
    """At session start (a cookie we haven't seen), reconnect the latest active
    connection so a fresh session resumes where the last one left off."""
    if _get_session_entry(sid):
        return
    stored = await _latest_active_connection()
    if stored is None:
        return
    state, _ = await _build_session(stored.name, stored.config, stored.database)
    if state is not None:
        _set_session_entry(sid, state)


async def get_session(sid: str) -> dict[str, Any]:
    """This session's state; auto-connects the latest active for an unseen cookie."""
    await _ensure_session(sid)
    s = _get_session_entry(sid)
    if s is None:
        return {"connected": False}
    return {
        "connected": True,
        "name": s.name,
        "databases": s.databases,
        "database": s.database,
    }


async def connect_new(sid: str, name: str, config: ChConfig) -> dict[str, Any]:
    """Create: open a config, save + activate it for this session."""
    state, message = await _build_session(name, config, None)
    if state is None:
        return {"ok": False, "message": message}
    _set_session_entry(sid, state)
    await _save_active_connection(name, config)
    return {"ok": True, "name": name, "databases": state.databases}


async def open_saved(sid: str, name: str) -> dict[str, Any]:
    """Open a saved connection by name for this session."""
    stored = await _connection_by_name(name)
    if stored is None:
        return {
            "ok": False,
            "message": f'no connection named "{name}"',
            "not_found": True,
        }
    # Reset the database so `connect <name>` always lands on the picker.
    state, message = await _build_session(stored.name, stored.config, None)
    if state is None:
        return {"ok": False, "message": message}
    _set_session_entry(sid, state)
    await _touch_connection(name)
    return {"ok": True, "name": name, "databases": state.databases}


async def select_database(sid: str, database: str) -> dict[str, Any]:
    """Select this session's active connection's database."""
    s = _get_session_entry(sid)
    if s is None:
        return {"ok": False, "message": "not connected", "reason": "no-session"}
    if not database or database not in s.databases:
        return {"ok": False, "message": "unknown database", "reason": "unknown"}
    s.database = database
    await _save_selected_database(s.name, database)
    return {"ok": True}
