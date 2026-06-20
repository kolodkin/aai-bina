"""The /api/db surface: unknown type is a 400; validation errors are 400;
the old /api/clickhouse paths are gone (404)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from queryview.main import app


def test_connect_unknown_type_is_400():
    c = TestClient(app)
    r = c.post("/api/db/connect", json={"type": "nope", "name": "x", "host": "h", "port": 1})
    assert r.status_code == 400
    assert "unknown" in r.json()["message"].lower()


def test_connect_validation_error_is_400():
    c = TestClient(app)
    r = c.post("/api/db/connect", json={"type": "clickhouse", "name": "x"})  # no host
    assert r.status_code == 400


def test_old_clickhouse_path_is_gone():
    c = TestClient(app)
    assert c.post("/api/clickhouse/connect", json={}).status_code == 404
