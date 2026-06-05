from playwright.sync_api import Page, expect


def test_query_against_seeded_db(seeded_test_db, page: Page, shot) -> None:
    # Connect using the form defaults (host=localhost, port=8123, user=default).
    page.goto("/", wait_until="networkidle")
    shot("landing prompt")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    shot("new connection form")
    page.get_by_test_id("ch-connect").click()

    # Pick the seeded `test` database.
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    shot("database picker")
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")

    # `query` reveals the panel.
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()
    shot("empty query panel")

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
    shot("saved predefined query")
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
    shot("results page 1 (limit 2)")

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
    shot("results page 2 (next)")


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


def test_cell_view_renders_link_and_custom_html(seeded_test_db, page: Page, shot) -> None:
    """Saving a predefined query with a cell_view YAML map renders cells per
    the map: `link` becomes an <a href> using {cell}, `custom` injects the
    template with {cell} HTML-escaped. Both wrappers carry an automatic
    data-testid="cell-<col>" so tests can target them without baking testids
    into the YAML. The YAML is authored in a modal opened from the toolbar."""
    _open_query_panel(page)

    page.get_by_test_id("query-input").fill("SELECT id, name FROM items ORDER BY id LIMIT 2")
    shot("panel - cell view toggle in toolbar (before Min)")

    # Name the query first — the modal Save is disabled without a name.
    page.once("dialog", lambda d: d.accept("with-views"))
    page.get_by_test_id("query-predefined-select").select_option("::new::")

    # Open the cell-view modal and author the YAML.
    page.get_by_test_id("cell-view-toggle").click()
    expect(page.get_by_test_id("cell-view-modal")).to_be_visible()
    page.get_by_test_id("cell-view-input").fill(
        "name:\n"
        "  type: link\n"
        "  value: https://example.com/{cell}\n"
        "id:\n"
        "  type: custom\n"
        "  value: <strong style=\"color:#a5b4fc\">{cell}</strong>\n"
    )
    shot("cell view modal - YAML authored")

    # Save persists cell_view + sql under the selected name and closes the modal.
    page.get_by_test_id("cell-view-save").click()
    expect(page.get_by_test_id("cell-view-modal")).not_to_be_visible()
    expect(
        page.get_by_test_id("query-predefined-select").locator(
            'option[value="with-views"]'
        )
    ).to_have_count(1)
    shot("saved - modal closed")

    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()

    # The `name` column renders as an <a href> built from the template and
    # carries an auto data-testid="cell-name" on the link itself.
    link = output.get_by_test_id("cell-name").first
    expect(link).to_be_visible()
    expect(link).to_have_text("alpha")
    expect(link).to_have_attribute("href", "https://example.com/alpha")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")

    # The `id` column renders the custom template wrapped in a span whose
    # auto data-testid="cell-id" exposes it without test markup in the YAML.
    custom = output.get_by_test_id("cell-id").first
    expect(custom).to_be_visible()
    expect(custom).to_have_text("1")
    expect(custom.locator("strong")).to_have_text("1")
    shot("results: name as link, id as custom HTML")


def test_cell_view_cancel_discards_edits(seeded_test_db, page: Page, shot) -> None:
    """Cancel in the cell-view modal closes without saving, so rendering still
    uses the saved cell_view (or none) — author-time edits never leak through."""
    _open_query_panel(page)

    page.get_by_test_id("query-input").fill("SELECT name FROM items ORDER BY id LIMIT 1")
    page.get_by_test_id("cell-view-toggle").click()
    expect(page.get_by_test_id("cell-view-modal")).to_be_visible()
    page.get_by_test_id("cell-view-input").fill(
        "name:\n  type: link\n  value: https://example.com/{cell}\n"
    )
    shot("cell view modal - draft YAML before Cancel")
    page.get_by_test_id("cell-view-cancel").click()
    expect(page.get_by_test_id("cell-view-modal")).not_to_be_visible()

    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    # Nothing saved => no cell_view applied => plain text, no <a>.
    expect(output.locator("a")).to_have_count(0)
    expect(output).to_contain_text("alpha")

    # Re-opening the modal shows the saved value (empty) — Cancel reverted the edit.
    page.get_by_test_id("cell-view-toggle").click()
    expect(page.get_by_test_id("cell-view-input")).to_have_value("")


def test_field_pickers_visibility_and_order_by(seeded_test_db, page: Page, shot) -> None:
    _open_query_panel(page)
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")

    # Fields describes the query's output columns and reveals both pickers.
    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"]')).to_have_count(2)
    expect(page.locator('[data-testid="field-toggle"][data-col="id"]')).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"][data-col="name"]')).to_be_visible()
    shot("fields described - both pickers")

    # Execute renders both columns.
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output.locator("table thead th")).to_have_count(2)
    shot("results with all columns")

    # Select fields is client-side: hiding `id` drops its column without re-running,
    # and the toggle immediately reflects the unselected state.
    id_toggle = page.locator('[data-testid="field-toggle"][data-col="id"]')
    id_toggle.click()
    expect(output.locator("table thead th")).to_have_count(1)
    expect(output.locator("table thead th")).to_contain_text("name")
    expect(id_toggle).to_have_attribute("data-on", "false")
    shot("id column hidden (client-side)")

    # Clear all hides every column; Select all restores them.
    page.get_by_test_id("fields-clear").click()
    expect(output.locator("table thead th")).to_have_count(0)
    shot("clear all - no columns")
    page.get_by_test_id("fields-select-all").click()
    expect(output.locator("table thead th")).to_have_count(2)
    shot("select all - columns restored")

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
    shot("order by name DESC selected")
    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("orderby-run").click()
    expect(output).to_contain_text("gamma")
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("alpha")
    shot("ordered + limited results (order-by Run)")


def test_query_param_dropdown_substitutes_value(seeded_test_db, page: Page, shot) -> None:
    """A `params:` block in the cell_view YAML renders a dropdown per param; the
    selected value is substituted into the SQL via {name} (auto-quoted as a
    string) and the query re-runs immediately on change."""
    _open_query_panel(page)

    page.get_by_test_id("query-input").fill(
        "SELECT name FROM items WHERE name = {sel} ORDER BY id"
    )

    # Name the query, then author a params block in the cell-view modal.
    page.once("dialog", lambda d: d.accept("by-name"))
    page.get_by_test_id("query-predefined-select").select_option("::new::")
    page.get_by_test_id("cell-view-toggle").click()
    expect(page.get_by_test_id("cell-view-modal")).to_be_visible()
    page.get_by_test_id("cell-view-input").fill(
        "params:\n"
        "  - name: sel\n"
        "    options: [alpha, beta, gamma]\n"
    )
    shot("cell view modal - params authored")
    page.get_by_test_id("cell-view-save").click()
    expect(page.get_by_test_id("cell-view-modal")).not_to_be_visible()

    # The dropdown renders with the declared options; default is the first one.
    sel = page.locator('[data-testid="param-select"][data-param="sel"]')
    expect(sel).to_be_visible()
    expect(sel.locator("option")).to_have_count(3)
    shot("params dropdown rendered")

    # Selecting a value auto-re-runs the query; substitution is quoted correctly
    # (an unquoted value would be a ClickHouse error, not a filtered result).
    sel.select_option("beta")
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("alpha")
    expect(output).not_to_contain_text("gamma")
    shot("query re-run with sel=beta")

    # Switching the value re-runs again with the new substitution.
    sel.select_option("gamma")
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("beta")
    shot("query re-run with sel=gamma")
