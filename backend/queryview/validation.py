"""Strict validation for the structured presentation fields (order_by, fields)
carried on a push or a predefined-query save. cell_view is intentionally NOT
validated here (lenient by design). Shared by remote.push and the save endpoint
so every write path enforces the same rules."""

from __future__ import annotations

MAX_LIMIT = 10000


def presentation_error(order_by: object, fields: object) -> str | None:
    """Return an error message if order_by/fields are malformed, else None.
    None inputs are valid (the field is simply absent/unchanged)."""
    if order_by is not None:
        if not isinstance(order_by, list):
            return "invalid order_by: must be a list"
        for col in order_by:
            if not isinstance(col, dict):
                return "invalid order_by: each entry must be an object"
            name = col.get("name")
            if not isinstance(name, str) or not name:
                return "invalid order_by: name must be a non-empty string"
            if "`" in name:
                return "invalid order_by: name must not contain a backtick"
            direction = col.get("dir")
            if not isinstance(direction, str) or direction.upper() not in ("ASC", "DESC"):
                return "invalid order_by: dir must be ASC or DESC"
    if fields is not None:
        if not isinstance(fields, list):
            return "invalid fields: must be a list"
        for f in fields:
            if not isinstance(f, str) or not f:
                return "invalid fields: each column must be a non-empty string"
    return None


def clamp_paging(limit: object, offset: object) -> tuple[int, int]:
    """Coerce/clamp limit & offset to safe ints: 0 <= limit <= MAX_LIMIT, offset >= 0."""
    try:
        lim = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        lim = 100
    try:
        off = int(offset)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        off = 0
    return max(0, min(lim, MAX_LIMIT)), max(0, off)
