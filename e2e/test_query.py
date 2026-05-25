from playwright.sync_api import Page, expect


def test_query_against_seeded_db(seeded_test_db, page: Page) -> None:
    # Connect using the form defaults (host=localhost, port=8123, user=default).
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    page.get_by_test_id("ch-connect").click()

    # Pick the seeded `test` database.
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")

    # `query` reveals the panel.
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()

    # Write a query.
    sql = "SELECT name FROM items ORDER BY id"
    page.get_by_test_id("query-input").fill(sql)

    # Predefined round-trip: save it, see it in the selector, reload it.
    page.get_by_test_id("query-save-name").fill("all items")
    page.get_by_test_id("query-save").click()
    select = page.get_by_test_id("query-predefined-select")
    expect(select.locator('option[value="all items"]')).to_have_count(1)
    page.get_by_test_id("query-input").fill("")
    select.select_option("all items")
    expect(page.get_by_test_id("query-input")).to_have_value(sql)

    # Pagination: limit 2 -> first page is alpha, beta (not gamma).
    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output.locator("table thead th")).to_contain_text("name")
    expect(output).to_contain_text("alpha")
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("gamma")

    # Download CSV of the current page.
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    csv_text = open(dl_info.value.path(), encoding="utf-8").read()
    assert "name" in csv_text
    assert "alpha" in csv_text

    # Next page: gamma (not alpha).
    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
