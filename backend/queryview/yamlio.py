"""YAML export/import of predefined queries and dashboards — one entity at a
time or a whole workspace as a single bundle. Every exported document is
self-describing via a top-level `kind` (query | dashboard | workspace), so
import dispatches on file content, never on the filename. Import validates the
whole document before writing anything, then upserts (same overwrite semantics
as gitsync restore). Docs: docs/export-import.md."""

from __future__ import annotations

import json
from typing import Any

import yaml

from .validation import presentation_error


class YamlIOError(Exception):
    """Export/import failure carrying an HTTP-ish status for the API layer:
    400 malformed document, 404 entity not found."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- Dumper (also used by gitsync's per-entity files) -----------------------


def _repr_str(dumper: yaml.SafeDumper, data: str):
    # Multiline strings (SQL, HTML, cell_view) as literal blocks for readable
    # files and diffs.
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _repr_str)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True)


# --- Document shapes --------------------------------------------------------


def _query_data(row: dict[str, Any], conn_type: str) -> dict[str, Any]:
    """One predefined-query row (list_predefined_queries shape: order_by/fields
    as JSON text) as its exported mapping. None/empty keys are omitted."""
    data: dict[str, Any] = {"type": conn_type, "query_name": row["query_name"], "query": row["query"]}
    if row.get("cell_view"):
        data["cell_view"] = row["cell_view"]
    if row.get("order_by"):
        data["order_by"] = json.loads(row["order_by"])
    if row.get("fields"):
        data["fields"] = json.loads(row["fields"])
    return data


def _dashboard_data(d: dict[str, Any]) -> dict[str, Any]:
    """One dashboard (get_dashboard shape) as its exported mapping."""
    return {
        "name": d["name"],
        "connection": d["connection"],
        "html": d["html"],
        "queries": d["queries"] or {},
    }


def _require_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise YamlIOError(f"malformed {where}: {key} must be a non-empty string")
    return value


def _parse_query_entry(data: Any, where: str = "query document") -> dict[str, Any]:
    """Validate one query mapping back to the DB row shape save_predefined_query
    takes (order_by/fields re-serialized to JSON text or None)."""
    if not isinstance(data, dict):
        raise YamlIOError(f"malformed {where}: expected a mapping")
    conn_type = _require_str(data, "type", where)
    query_name = _require_str(data, "query_name", where)
    query = _require_str(data, "query", where)
    cell_view = data.get("cell_view")
    if cell_view is not None and not isinstance(cell_view, str):
        raise YamlIOError(f"malformed {where}: cell_view must be a string")
    ob, fl = data.get("order_by"), data.get("fields")
    perr = presentation_error(ob, fl)
    if perr is not None:
        raise YamlIOError(f"malformed {where}: {perr}")
    return {
        "type": conn_type,
        "query_name": query_name,
        "query": query,
        "cell_view": cell_view or None,
        "order_by": json.dumps(ob) if ob else None,
        "fields": json.dumps(fl) if fl else None,
    }


def _parse_dashboard_entry(data: Any, where: str = "dashboard document") -> dict[str, Any]:
    """Validate one dashboard mapping back to the upsert_dashboard shape."""
    if not isinstance(data, dict):
        raise YamlIOError(f"malformed {where}: expected a mapping")
    name = _require_str(data, "name", where)
    connection = _require_str(data, "connection", where)
    html = _require_str(data, "html", where)
    queries = data.get("queries") or {}
    if not isinstance(queries, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in queries.items()):
        raise YamlIOError(f"malformed {where}: queries must be a {{name: SQL}} map")
    return {"name": name, "connection": connection, "html": html, "queries": queries}


def parse_document(text: str) -> tuple[str, Any]:
    """Parse + validate an import document. Returns (kind, payload): for
    kind 'query'/'dashboard' the payload is one parsed entry; for 'workspace'
    a {'queries': [...], 'dashboards': [...]} pair of parsed entries."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise YamlIOError(f"malformed YAML: {e}") from e
    if not isinstance(data, dict):
        raise YamlIOError("malformed document: expected a YAML mapping")
    kind = data.get("kind")
    if kind == "query":
        return "query", _parse_query_entry(data)
    if kind == "dashboard":
        return "dashboard", _parse_dashboard_entry(data)
    if kind == "workspace":
        raw_queries = data.get("queries") or []
        raw_dashboards = data.get("dashboards") or []
        if not isinstance(raw_queries, list) or not isinstance(raw_dashboards, list):
            raise YamlIOError("malformed workspace document: queries and dashboards must be lists")
        return "workspace", {
            "queries": [_parse_query_entry(q, f"workspace queries[{i}]") for i, q in enumerate(raw_queries)],
            "dashboards": [
                _parse_dashboard_entry(d, f"workspace dashboards[{i}]") for i, d in enumerate(raw_dashboards)
            ],
        }
    raise YamlIOError("malformed document: kind must be 'query', 'dashboard' or 'workspace'")


# --- Services ---------------------------------------------------------------


async def export_query(conn_type: str, name: str, workspace_id: int) -> str:
    from .queries import get_predefined_query

    row = await get_predefined_query(conn_type, name, workspace_id)
    if row is None:
        raise YamlIOError(f"query {name!r} not found", status=404)
    return dump_yaml({"kind": "query", **_query_data(row, conn_type)})


async def export_dashboard(name: str, workspace_id: int) -> str:
    from .dashboards import get_dashboard

    d = await get_dashboard(name, workspace_id)
    if d is None:
        raise YamlIOError(f"dashboard {name!r} not found", status=404)
    return dump_yaml({"kind": "dashboard", **_dashboard_data(d)})


async def export_workspace(workspace_id: int) -> str:
    """The whole workspace (every predefined query and dashboard) as one
    bundle. Empty workspaces export a valid, importable empty bundle."""
    from .dashboards import list_dashboards_full
    from .queries import list_all_predefined_queries

    queries = await list_all_predefined_queries(workspace_id)
    dashboards = await list_dashboards_full(workspace_id)
    return dump_yaml(
        {
            "kind": "workspace",
            "queries": [_query_data(r, r["type"]) for r in queries],
            "dashboards": [_dashboard_data(d) for d in dashboards],
        }
    )


async def _import_query(row: dict[str, Any], workspace_id: int) -> None:
    from .queries import save_predefined_query

    await save_predefined_query(
        row["query_name"],
        row["type"],
        row["query"],
        row["cell_view"],
        row["order_by"],
        row["fields"],
        workspace_id=workspace_id,
    )


async def _import_dashboard(d: dict[str, Any], workspace_id: int) -> None:
    from .dashboards import upsert_dashboard

    await upsert_dashboard(d["name"], d["connection"], d["html"], d["queries"], workspace_id=workspace_id)


async def import_text(text: str, workspace_id: int) -> dict[str, Any]:
    """Import a document into a workspace, upserting by name. The whole
    document is validated before the first write, so a malformed file changes
    nothing. Returns {'kind', 'queries', 'dashboards'} counts."""
    kind, payload = parse_document(text)
    if kind == "query":
        await _import_query(payload, workspace_id)
        return {"kind": kind, "queries": 1, "dashboards": 0}
    if kind == "dashboard":
        await _import_dashboard(payload, workspace_id)
        return {"kind": kind, "queries": 0, "dashboards": 1}
    for row in payload["queries"]:
        await _import_query(row, workspace_id)
    for d in payload["dashboards"]:
        await _import_dashboard(d, workspace_id)
    return {"kind": kind, "queries": len(payload["queries"]), "dashboards": len(payload["dashboards"])}
