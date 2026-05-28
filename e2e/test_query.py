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


def test_cell_view_renders_link_and_custom_html(seeded_test_db, page: Page) -> None:
    """Saving a predefined query with a cell_view YAML map renders cells per
    the map: `link` becomes an <a href> using {cell}, `custom` injects the
    template with {cell} HTML-escaped."""
    _open_query_panel(page)

    page.get_by_test_id("query-input").fill("SELECT id, name FROM items ORDER BY id LIMIT 2")
    page.get_by_test_id("cell-view-input").fill(
        "name:\n"
        "  type: link\n"
        "  value: https://example.com/{cell}\n"
        "id:\n"
        "  type: custom\n"
        "  value: <strong data-testid=\"id-strong\">{cell}</strong>\n"
    )

    # Name the query and Save (Save persists cell_view; loadPredefined refreshes
    # the saved value into state so rendering can pick it up).
    page.once("dialog", lambda d: d.accept("with-views"))
    page.get_by_test_id("query-predefined-select").select_option("::new::")
    page.get_by_test_id("query-save").click()
    expect(
        page.get_by_test_id("query-predefined-select").locator(
            'option[value="with-views"]'
        )
    ).to_have_count(1)

    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()

    # The `name` column renders as an <a href> built from the template.
    link = output.locator('a[href="https://example.com/alpha"]')
    expect(link).to_be_visible()
    expect(link).to_have_text("alpha")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")

    # The `id` column renders the custom template tag with the cell substituted.
    strong = output.get_by_test_id("id-strong").first
    expect(strong).to_be_visible()
    expect(strong).to_have_text("1")


def test_cell_view_only_applies_after_save(seeded_test_db, page: Page) -> None:
    """Edits to the cell_view editor must NOT affect rendering until Save —
    rendering uses the saved cell_view of the active predefined query."""
    _open_query_panel(page)

    page.get_by_test_id("query-input").fill("SELECT name FROM items ORDER BY id LIMIT 1")
    # Set views in the editor but DO NOT save.
    page.get_by_test_id("cell-view-input").fill(
        "name:\n  type: link\n  value: https://example.com/{cell}\n"
    )
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    # No predefined query selected => no saved cell_view applies => plain text.
    expect(output.locator("a")).to_have_count(0)
    expect(output).to_contain_text("alpha")


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
