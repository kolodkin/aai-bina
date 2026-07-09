"""Git sync: per-entity backup/restore of predefined queries and dashboards to
a configured git remote. Versions are git commits — store makes one commit per
entity, restore reads objects at a ref (git show) and upserts the DB row; HEAD
never moves. Spec: docs/superpowers/specs/2026-07-08-git-sync-design.md."""

from __future__ import annotations

import json
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
