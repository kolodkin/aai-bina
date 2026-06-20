from playwright.sync_api import Page, expect


def test_duckdb_connect_no_picker_and_query(seeded_duckdb, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new duckdb")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("duckdb-form")).to_be_visible()

    # Point at the seeded file and connect.
    page.get_by_test_id("duck-path").fill(seeded_duckdb)
    shot("duckdb connection form")
    page.get_by_test_id("duck-connect").click()

    # No picker for DuckDB: it goes straight to ready (indicator shows the name).
    expect(page.get_by_test_id("db-picker")).to_have_count(0)
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - duckdb")
    shot("connected to duckdb (no picker)")

    # Open the query panel directly and query the file.
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items ORDER BY id")
    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output.locator("table thead th")).to_contain_text("name")
    expect(output).to_contain_text("alpha")
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("gamma")
    shot("duckdb results page 1")

    # CSV + next page.
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    csv_text = open(dl_info.value.path(), encoding="utf-8").read()
    assert "name" in csv_text and "alpha" in csv_text
    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
    shot("duckdb results page 2")


def test_duckdb_fields_describe(seeded_duckdb, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new duckdb")
    page.keyboard.press("Enter")
    page.get_by_test_id("duck-path").fill(seeded_duckdb)
    page.get_by_test_id("duck-connect").click()
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")
    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"]')).to_have_count(2)
    expect(page.locator('[data-testid="field-toggle"][data-col="id"]')).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"][data-col="name"]')).to_be_visible()
    shot("duckdb describe fields")
