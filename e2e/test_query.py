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


def test_cell_view_row_placeholder_references_other_columns(
    seeded_test_db, page: Page, shot
) -> None:
    """A cell_view template can reference other columns of the same row via
    `{row.<col>}`. Here the `name` column renders as a link whose href is built
    from a sibling column (`{row.id}`), and a `custom` cell combines `{cell}`
    with `{row.name}` — each row resolves against its own values."""
    _open_query_panel(page)

    page.get_by_test_id("query-input").fill("SELECT id, name FROM items ORDER BY id LIMIT 2")
    page.once("dialog", lambda d: d.accept("with-row-refs"))
    page.get_by_test_id("query-predefined-select").select_option("::new::")

    page.get_by_test_id("cell-view-toggle").click()
    expect(page.get_by_test_id("cell-view-modal")).to_be_visible()
    page.get_by_test_id("cell-view-input").fill(
        "name:\n"
        "  type: link\n"
        "  value: https://example.com/items/{row.id}\n"
        "id:\n"
        "  type: custom\n"
        "  value: <em>{cell}:{row.name}</em>\n"
    )
    shot("cell view modal - row.* placeholders authored")
    page.get_by_test_id("cell-view-save").click()
    expect(page.get_by_test_id("cell-view-modal")).not_to_be_visible()

    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()

    # First row (id=1, name=alpha): the `name` link's href uses {row.id}.
    link = output.get_by_test_id("cell-name").first
    expect(link).to_have_text("alpha")
    expect(link).to_have_attribute("href", "https://example.com/items/1")

    # The `id` custom cell combines {cell} (=1) with {row.name} (=alpha).
    custom = output.get_by_test_id("cell-id").first
    expect(custom.locator("em")).to_have_text("1:alpha")
    shot("results: row.* placeholders resolved per row")


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


def _author_params_yaml(page: Page, name: str, sql: str, params_yaml: str) -> None:
    """Fill the SQL, name the query, and author a `params:` block via the
    cell-view modal, then Save. Shared by the query-param tests below."""
    page.get_by_test_id("query-input").fill(sql)
    page.once("dialog", lambda d: d.accept(name))
    page.get_by_test_id("query-predefined-select").select_option("::new::")
    page.get_by_test_id("cell-view-toggle").click()
    expect(page.get_by_test_id("cell-view-modal")).to_be_visible()
    page.get_by_test_id("cell-view-input").fill(params_yaml)
    page.get_by_test_id("cell-view-save").click()
    expect(page.get_by_test_id("cell-view-modal")).not_to_be_visible()


def test_query_param_dropdown_substitutes_value(seeded_test_db, page: Page, shot) -> None:
    """A `params:` block in the cell_view YAML renders a dropdown per param; the
    selected value is substituted into the SQL via {name} (auto-quoted as a
    string) and the query re-runs immediately on change."""
    _open_query_panel(page)
    _author_params_yaml(
        page,
        "by-name",
        "SELECT name FROM items WHERE name = {sel} ORDER BY id",
        "params:\n"
        "  - name: sel\n"
        "    options: [alpha, beta, gamma]\n",
    )

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


def test_query_param_options_sql_populates_dropdown(seeded_test_db, page: Page, shot) -> None:
    """`options_sql` resolves the dropdown choices from a query: the first column
    of every row becomes an option, in the query's own order. The first row is
    the default; substituting it and running returns that row, and switching the
    selection re-runs with the new value."""
    _open_query_panel(page)
    _author_params_yaml(
        page,
        "by-options-sql",
        "SELECT name FROM items WHERE name = {sel} ORDER BY id",
        "params:\n"
        "  - name: sel\n"
        "    options_sql: SELECT DISTINCT name FROM items ORDER BY name\n",
    )

    # The dropdown is populated from the query result: alpha, beta, gamma.
    sel = page.locator('[data-testid="param-select"][data-param="sel"]')
    expect(sel).to_be_visible()
    expect(sel.locator("option")).to_have_count(3)
    expect(sel.locator("option").first).to_have_text("alpha")
    shot("options_sql dropdown populated")

    # Default is the first option (alpha); running substitutes and returns it.
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output).to_contain_text("alpha")
    expect(output).not_to_contain_text("beta")
    shot("options_sql default (alpha) run")

    # Switching re-runs with the new substitution.
    sel.select_option("gamma")
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
    shot("options_sql re-run with sel=gamma")


def test_query_param_options_and_options_sql_are_mutually_exclusive(
    seeded_test_db, page: Page, shot
) -> None:
    """A param declaring both `options` and `options_sql` is a config error: the
    entry is dropped during parse, so no dropdown renders for it."""
    _open_query_panel(page)
    _author_params_yaml(
        page,
        "both-keys",
        "SELECT name FROM items WHERE name = {sel} ORDER BY id",
        "params:\n"
        "  - name: sel\n"
        "    options: [alpha, beta]\n"
        "    options_sql: SELECT name FROM items\n",
    )
    expect(page.locator('[data-testid="param-select"][data-param="sel"]')).to_have_count(0)
    shot("both options + options_sql -> no dropdown")


def test_query_param_options_sql_no_rows_blocks_run(seeded_test_db, page: Page, shot) -> None:
    """An `options_sql` that returns zero rows has nothing to choose from: the
    main query is blocked (Execute disabled) and the error banner names the
    param and the empty result."""
    _open_query_panel(page)
    _author_params_yaml(
        page,
        "empty-options",
        "SELECT name FROM items WHERE name = {sel}",
        "params:\n"
        "  - name: sel\n"
        "    options_sql: SELECT name FROM items WHERE 1 = 0\n",
    )
    error = page.get_by_test_id("query-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text('options for "sel"')
    expect(error).to_contain_text("no rows")
    expect(page.get_by_test_id("query-run")).to_be_disabled()
    shot("options_sql empty -> blocked")


def test_query_param_options_sql_error_blocks_run(seeded_test_db, page: Page, shot) -> None:
    """An `options_sql` that errors blocks the main query (Execute disabled) and
    surfaces the failure through the banner, prefixed with the param name."""
    _open_query_panel(page)
    _author_params_yaml(
        page,
        "bad-options",
        "SELECT name FROM items WHERE name = {sel}",
        "params:\n"
        "  - name: sel\n"
        "    options_sql: SELECT name FROM no_such_table\n",
    )
    error = page.get_by_test_id("query-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text('options for "sel"')
    expect(page.get_by_test_id("query-run")).to_be_disabled()
    shot("options_sql error -> blocked")
