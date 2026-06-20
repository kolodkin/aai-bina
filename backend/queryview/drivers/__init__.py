"""Driver registry: maps a connection `type` to the Driver that executes it.
Task 2 adds the ClickHouse driver; Plans 2 and 3 append Postgres / DuckDB."""
from __future__ import annotations

from .base import Driver, QueryResult, build_order_by, serialize_rows, wrap_paginated

__all__ = [
    "Driver",
    "QueryResult",
    "build_order_by",
    "serialize_rows",
    "wrap_paginated",
]
