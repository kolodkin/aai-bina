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

    # Predefined round-trip: name it via the dropdown's "New name…" item (a
    # prompt), Save under that name, see it in the selector, reload it.
    select = page.get_by_test_id("query-predefined-select")
    page.once("dialog", lambda d: d.accept("all items"))
    select.select_option("::new::")
    page.get_by_test_id("query-save").click()
    expect(select.locator('option[value="all items"]')).to_have_count(1)
    select.select_option("")
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


def _open_query_panel(page: Page) -> None:
    """Connect with form defaults, select the seeded `test` db, open the panel."""
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    page.get_by_test_id("ch-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()


def test_field_pickers_visibility_and_order_by(seeded_test_db, page: Page) -> None:
    _open_query_panel(page)
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")

    # Fields describes the query's output columns and reveals both pickers.
    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"]')).to_have_count(2)
    expect(page.locator('[data-testid="field-toggle"][data-col="id"]')).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"][data-col="name"]')).to_be_visible()

    # Execute renders both columns.
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output.locator("table thead th")).to_have_count(2)

    # Select fields is client-side: hiding `id` drops its column without re-running,
    # and the toggle immediately reflects the unselected state.
    id_toggle = page.locator('[data-testid="field-toggle"][data-col="id"]')
    id_toggle.click()
    expect(output.locator("table thead th")).to_have_count(1)
    expect(output.locator("table thead th")).to_contain_text("name")
    expect(id_toggle).to_have_attribute("data-on", "false")

    # Clear all hides every column; Select all restores them.
    page.get_by_test_id("fields-clear").click()
    expect(output.locator("table thead th")).to_have_count(0)
    page.get_by_test_id("fields-select-all").click()
    expect(output.locator("table thead th")).to_have_count(2)

    # CSV exports all columns regardless of visibility: hide `id`, it's still in the CSV.
    page.locator('[data-testid="field-toggle"][data-col="id"]').click()
    expect(output.locator("table thead th")).to_have_count(1)
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    header = open(dl_info.value.path(), encoding="utf-8").read().splitlines()[0]
    assert "id" in header
    assert "name" in header

    # Order by name DESC (server-side): selecting it does NOT change results until
    # the query re-runs. The order-by Run button re-runs the query (like Execute),
    # so it also applies the current limit.
    page.locator('[data-testid="orderby-add"][data-col="name"]').click()
    chip = page.locator('[data-testid="orderby-chip"][data-col="name"]')
    expect(chip).to_be_visible()
    chip.get_by_test_id("orderby-dir").click()  # ASC -> DESC
    expect(chip.get_by_test_id("orderby-dir")).to_have_text("DESC")
    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("orderby-run").click()
    expect(output).to_contain_text("gamma")
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("alpha")
