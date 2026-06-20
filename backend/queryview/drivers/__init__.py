"""Driver registry: maps a connection `type` to the Driver that executes it.
Plans 2 and 3 append PostgresDriver / DuckDBDriver to DRIVERS."""
from __future__ import annotations

from .base import Driver, QueryResult, build_order_by, serialize_rows, wrap_paginated
from .clickhouse import ClickHouseDriver
from .duckdb import DuckDBDriver
from .postgres import PostgresDriver

DRIVERS: dict[str, Driver] = {
    d.type: d for d in (ClickHouseDriver(), PostgresDriver(), DuckDBDriver())
}

__all__ = [
    "Driver",
    "QueryResult",
    "DRIVERS",
    "build_order_by",
    "serialize_rows",
    "wrap_paginated",
]
