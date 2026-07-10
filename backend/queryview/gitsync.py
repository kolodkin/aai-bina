"""Git sync: per-entity backup/restore of predefined queries and dashboards to
a configured git remote. Versions are git commits — store makes one commit per
entity, restore reads objects at a ref (git show) and upserts the DB row; HEAD
never moves. Docs: docs/gitsync.md."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml


class GitSyncError(Exception):
    """Git-sync failure carrying an HTTP-ish status for the API layer:
    409 unconfigured, 404 entity/ref not found, 502 git or parse failure."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# --- Serialization ---------------------------------------------------------


def _repr_str(dumper: yaml.SafeDumper, data: str):
    # Multiline strings (SQL, cell_view) as literal blocks for readable diffs.
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _repr_str)


def _dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True)


_SAFE = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._ -"
)


def slug(name: str) -> str:
    """Filesystem-safe name: percent-encode (UTF-8) anything outside
    [A-Za-z0-9._ -] plus a leading dot. The canonical name lives inside the
    YAML — restore trusts the file content, never the path."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch in _SAFE and not (ch == "." and i == 0):
            out.append(ch)
        else:
            out.append("".join(f"%{b:02X}" for b in ch.encode("utf-8")))
    return "".join(out)


def query_relpath(conn_type: str, name: str) -> str:
    return f"queries/{slug(conn_type)}/{slug(name)}.yaml"


def dashboard_reldir(name: str) -> str:
    return f"dashboards/{slug(name)}"


def query_to_yaml(row: dict[str, Any]) -> str:
    """One predefined-query row (as returned by list_predefined_queries) as
    YAML. cell_view stays a verbatim string; order_by/fields are stored in the
    DB as JSON text and exported as parsed YAML values. None keys are omitted."""
    data: dict[str, Any] = {"query_name": row["query_name"], "query": row["query"]}
    if row.get("cell_view"):
        data["cell_view"] = row["cell_view"]
    if row.get("order_by"):
        data["order_by"] = json.loads(row["order_by"])
    if row.get("fields"):
        data["fields"] = json.loads(row["fields"])
    return _dump(data)


def query_from_yaml(text: str) -> dict[str, Any]:
    """Inverse of query_to_yaml, back to the DB row shape (order_by/fields as
    JSON text or None)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise GitSyncError(f"malformed query file: {e}") from e
    if not isinstance(data, dict) or not data.get("query_name") or not data.get("query"):
        raise GitSyncError("malformed query file: query_name and query are required")
    ob, fl = data.get("order_by"), data.get("fields")
    return {
        "query_name": data["query_name"],
        "query": data["query"],
        "cell_view": data.get("cell_view") or None,
        "order_by": json.dumps(ob) if ob else None,
        "fields": json.dumps(fl) if fl else None,
    }


def dashboard_to_files(d: dict[str, Any]) -> dict[str, str]:
    """A dashboard (as returned by get_dashboard) as its three repo files."""
    return {
        "meta.yaml": _dump({"name": d["name"], "connection": d["connection"]}),
        "dashboard.html": d["html"],
        "queries.yaml": _dump(d["queries"] or {}),
    }


def dashboard_from_files(files: dict[str, str]) -> dict[str, Any]:
    """Inverse of dashboard_to_files."""
    try:
        meta = yaml.safe_load(files.get("meta.yaml") or "")
        queries = yaml.safe_load(files.get("queries.yaml") or "") or {}
    except yaml.YAMLError as e:
        raise GitSyncError(f"malformed dashboard file: {e}") from e
    if not isinstance(meta, dict) or not meta.get("name") or not meta.get("connection"):
        raise GitSyncError("malformed meta.yaml: name and connection are required")
    if not isinstance(queries, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in queries.items()
    ):
        raise GitSyncError("malformed queries.yaml: expected {name: SQL} map")
    return {
        "name": meta["name"],
        "connection": meta["connection"],
        "html": files.get("dashboard.html", ""),
        "queries": queries,
    }


# --- Configuration ---------------------------------------------------------


def _remote() -> str:
    remote = os.environ.get("GIT_SYNC_REMOTE")
    if not remote:
        raise GitSyncError("git sync is not configured (set GIT_SYNC_REMOTE)", status=409)
    return remote


def _branch() -> str:
    return os.environ.get("GIT_SYNC_BRANCH", "main")


def _workdir() -> Path:
    env = os.environ.get("GIT_SYNC_DIR")
    if env:
        return Path(env)
    from .connect import _db_path

    return Path(f"{_db_path()}.gitsync")


def configured() -> bool:
    return bool(os.environ.get("GIT_SYNC_REMOTE"))


# --- Git plumbing ----------------------------------------------------------

# One git operation at a time; the workdir is shared mutable state. The lock is
# per event loop (asyncio.Lock binds to the loop that first acquires it, and
# tests run each operation under a fresh asyncio.run loop); production has a
# single loop, so this is one lock there.
_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    key = id(asyncio.get_running_loop())
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


async def _git(*args: str, cwd: Path | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip()[-500:]
        raise GitSyncError(f"git {args[0]} failed: {tail}")
    return out.decode("utf-8", "replace")


async def _ensure_repo() -> Path:
    """The sync clone, cloning (or, for an empty remote, init + remote add) on
    first use. Idempotent."""
    remote, branch, wd = _remote(), _branch(), _workdir()
    if (wd / ".git").exists():
        return wd
    wd.parent.mkdir(parents=True, exist_ok=True)
    try:
        await _git("clone", "--branch", branch, remote, str(wd))
    except GitSyncError as clone_err:
        # Clone can fail either because the branch genuinely doesn't exist yet
        # on an empty remote (the only case we should paper over with a local
        # init) or because the remote itself is unreachable/misconfigured. Ask
        # the remote directly to tell the two apart.
        try:
            heads = await _git("ls-remote", "--heads", remote, branch)
        except GitSyncError as probe_err:
            raise GitSyncError(f"git remote unreachable: {probe_err}") from probe_err
        if heads.strip():
            raise clone_err
        # Empty remote (branch doesn't exist yet): start locally, attach remote.
        if wd.exists():
            shutil.rmtree(wd)  # clean up any partial clone before init
        await _git("init", "-b", branch, str(wd))
        await _git("remote", "add", "origin", remote, cwd=wd)
    await _git("config", "user.name", "queryview", cwd=wd)
    await _git("config", "user.email", "queryview@localhost", cwd=wd)
    return wd


async def _origin_head(wd: Path) -> str | None:
    """Fetch, then the remote branch ref if it exists (None on an empty remote).
    Network/auth failures raise."""
    await _git("fetch", "origin", cwd=wd)
    ref = f"origin/{_branch()}"
    try:
        await _git("rev-parse", "--verify", ref, cwd=wd)
    except GitSyncError:
        return None
    return ref


# --- Entities --------------------------------------------------------------


def _check_kind(kind: str, conn_type: str | None) -> None:
    """Validate kind/conn_type here so every caller (REST, MCP, future ones)
    inherits it instead of each layer re-implementing the check."""
    if kind not in ("query", "dashboard"):
        raise GitSyncError(
            f"unknown kind {kind!r} (expected 'query' or 'dashboard')", status=400
        )
    if kind == "query" and not conn_type:
        raise GitSyncError("conn_type is required for queries", status=400)


def entity_relpath(kind: str, name: str, conn_type: str | None) -> str:
    return query_relpath(conn_type or "", name) if kind == "query" else dashboard_reldir(name)


async def _load_entity(kind: str, name: str, conn_type: str | None) -> dict[str, Any]:
    if kind == "query":
        from .queries import get_predefined_query
        from .workspaces import DEFAULT_WORKSPACE, resolve as resolve_workspace

        ws = await resolve_workspace(DEFAULT_WORKSPACE)  # interim until Task 5
        row = await get_predefined_query(conn_type or "", name, ws.id)
        if row is None:
            raise GitSyncError(f"query {name!r} not found", status=404)
        return row
    from .dashboards import get_dashboard

    d = await get_dashboard(name)
    if d is None:
        raise GitSyncError(f"dashboard {name!r} not found", status=404)
    return d


# --- Operations ------------------------------------------------------------


async def store(
    kind: str,
    name: str,
    conn_type: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Export one entity's saved DB state into the clone, commit, push.
    The workdir is reset to the remote head first — exports are deterministic
    from the DB and each commit touches one entity, so this is always safe and
    avoids push rejections."""
    _check_kind(kind, conn_type)
    _remote()  # unconfigured -> 409 before any DB/entity lookup
    entity = await _load_entity(kind, name, conn_type)
    async with _lock():
        wd = await _ensure_repo()
        head = await _origin_head(wd)
        if head:
            await _git("reset", "--hard", head, cwd=wd)
        relpath = entity_relpath(kind, name, conn_type)
        if kind == "query":
            path = wd / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(query_to_yaml(entity), encoding="utf-8")
        else:
            ddir = wd / relpath
            if ddir.exists():
                shutil.rmtree(ddir)
            ddir.mkdir(parents=True, exist_ok=True)
            for fname, content in dashboard_to_files(entity).items():
                (ddir / fname).write_text(content, encoding="utf-8")
        await _git("add", "-A", "--", relpath, cwd=wd)
        if not (await _git("status", "--porcelain", "--", relpath, cwd=wd)).strip():
            return {"committed": False, "sha": None, "message": "no changes"}
        label = f"{conn_type}/{name}" if kind == "query" else name
        await _git("commit", "-m", message or f"store {kind} {label}", cwd=wd)
        await _git("push", "origin", _branch(), cwd=wd)
        sha = (await _git("rev-parse", "HEAD", cwd=wd)).strip()
        return {"committed": True, "sha": sha, "message": "stored"}


async def history(
    kind: str,
    name: str,
    conn_type: str | None = None,
    before: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Commits touching the entity's path, newest first. `before=<sha>` pages
    strictly older commits. Reads objects only — never touches the working tree."""
    _check_kind(kind, conn_type)
    _remote()
    relpath = entity_relpath(kind, name, conn_type)
    async with _lock():
        wd = await _ensure_repo()
        head = await _origin_head(wd)
        if head is None:
            return {"revisions": [], "has_more": False}
        start = head
        if before:
            try:
                await _git("rev-parse", "--verify", "--quiet", f"{before}^{{commit}}", cwd=wd)
            except GitSyncError:
                raise GitSyncError(f"unknown revision {before!r}", status=404)
            try:
                await _git("rev-parse", "--verify", "--quiet", f"{before}^", cwd=wd)
            except GitSyncError:
                # `before` is the oldest commit: <sha>^ doesn't resolve.
                return {"revisions": [], "has_more": False}
            start = f"{before}^"
        out = await _git(
            "log",
            f"--max-count={limit + 1}",
            "--format=%H%x1f%ct%x1f%s",
            start,
            "--",
            relpath,
            cwd=wd,
        )
    revisions = []
    for line in out.splitlines():
        sha, ct, subject = line.split("\x1f", 2)
        revisions.append({"sha": sha, "date": int(ct) * 1000, "message": subject})
    return {"revisions": revisions[:limit], "has_more": len(revisions) > limit}


async def restore(
    kind: str,
    name: str,
    conn_type: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """Overwrite the local DB row with the entity's content at `ref` (default:
    the remote branch head). Reads via `git show` — HEAD never moves, history
    is never rewritten. Parses fully before writing, so the DB row is either
    untouched or fully replaced."""
    _check_kind(kind, conn_type)
    _remote()
    relpath = entity_relpath(kind, name, conn_type)
    async with _lock():
        wd = await _ensure_repo()
        head = await _origin_head(wd)
        resolved = ref if ref and ref != "HEAD" else head
        if resolved is None:
            raise GitSyncError(f"{kind} {name!r} not found in git", status=404)

        async def _show(path: str) -> str:
            return await _git("show", f"{resolved}:{path}", cwd=wd)

        if kind == "query":
            try:
                text = await _show(relpath)
            except GitSyncError:
                raise GitSyncError(
                    f"query {name!r} not found at {resolved}", status=404
                ) from None
            data = query_from_yaml(text)
        else:
            files: dict[str, str] = {}
            for fname in ("meta.yaml", "dashboard.html", "queries.yaml"):
                try:
                    files[fname] = await _show(f"{relpath}/{fname}")
                except GitSyncError:
                    if fname == "meta.yaml":
                        raise GitSyncError(
                            f"dashboard {name!r} not found at {resolved}", status=404
                        ) from None
            data = dashboard_from_files(files)

    # DB upsert happens outside the git lock — it doesn't touch the workdir.
    if kind == "query":
        from .queries import save_predefined_query
        from .workspaces import DEFAULT_WORKSPACE, resolve as resolve_workspace

        ws = await resolve_workspace(DEFAULT_WORKSPACE)  # interim until Task 5
        await save_predefined_query(
            data["query_name"],
            conn_type or "",
            data["query"],
            data["cell_view"],
            data["order_by"],
            data["fields"],
            workspace_id=ws.id,
        )
    else:
        from .dashboards import upsert_dashboard

        await upsert_dashboard(
            data["name"], data["connection"], data["html"], data["queries"]
        )
    return {"restored": True, "sha": resolved}
