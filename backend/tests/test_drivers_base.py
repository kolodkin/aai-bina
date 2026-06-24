"""Driver dialect helpers and the row serializer (the shared output contract)."""
from __future__ import annotations

from queryview.drivers.base import build_order_by, serialize_rows, wrap_paginated


def test_build_order_by_quotes_and_whitelists_direction():
    assert build_order_by([{"name": "a", "dir": "desc"}], "`") == "ORDER BY `a` DESC"
    # Unknown direction falls back to ASC; quote chars in the name are doubled.
    assert build_order_by([{"name": "a`b", "dir": "x"}], "`") == "ORDER BY `a``b` ASC"
    assert build_order_by([{"name": "a"}], '"') == 'ORDER BY "a" ASC'
    assert build_order_by(None, "`") == ""
    assert build_order_by([{"bad": 1}], "`") == ""


def test_wrap_paginated_matches_clickhouse_shape_without_alias():
    out = wrap_paginated("SELECT 1;", "", 100, 0, alias=None)
    assert out == "SELECT * FROM (\nSELECT 1\n) LIMIT 100 OFFSET 0"


def test_wrap_paginated_adds_alias_and_order():
    out = wrap_paginated("SELECT 1", 'ORDER BY "a" ASC', 10, 5, alias="_qv")
    assert out == 'SELECT * FROM (\nSELECT 1\n) AS _qv ORDER BY "a" ASC LIMIT 10 OFFSET 5'


def test_serialize_rows_tsv_with_names_and_nulls():
    out = serialize_rows(["id", "name"], [[1, "a"], [2, None]], "tsv")
    assert out == "id\tname\n1\ta\n2\t"


def test_serialize_rows_csv_quotes_and_uses_lf():
    out = serialize_rows(["a", "b"], [["x,y", "z"]], "csv")
    assert out == 'a,b\n"x,y",z'


def test_serialize_rows_empty_is_just_header():
    assert serialize_rows(["a"], [], "tsv") == "a"
