from queryview.validation import MAX_LIMIT, clamp_paging, presentation_error


def test_none_inputs_are_valid():
    assert presentation_error(None, None) is None


def test_valid_order_by_and_fields():
    assert presentation_error([{"name": "id", "dir": "DESC"}], ["id", "name"]) is None
    assert presentation_error([{"name": "id", "dir": "asc"}], None) is None  # case-insensitive


def test_bad_direction_rejected():
    msg = presentation_error([{"name": "id", "dir": "SIDEWAYS"}], None)
    assert msg is not None and "dir" in msg


def test_missing_name_rejected():
    assert presentation_error([{"dir": "ASC"}], None) is not None


def test_backtick_in_name_rejected():
    assert presentation_error([{"name": "a`b", "dir": "ASC"}], None) is not None


def test_non_list_order_by_rejected():
    assert presentation_error("id DESC", None) is not None


def test_non_string_field_rejected():
    assert presentation_error(None, ["ok", 3]) is not None
    assert presentation_error(None, ["ok", ""]) is not None


def test_clamp_paging():
    assert clamp_paging(50, 10) == (50, 10)
    assert clamp_paging(-5, -5) == (0, 0)
    assert clamp_paging(MAX_LIMIT + 1, 0) == (MAX_LIMIT, 0)
    assert clamp_paging("x", None) == (100, 0)
