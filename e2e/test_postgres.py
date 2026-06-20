from playwright.sync_api import Page, expect


def test_postgres_connect_pick_db_and_query(seeded_pg_db, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new postgres")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("postgres-form")).to_be_visible()
    shot("postgres connection form")
    page.get_by_test_id("pg-connect").click()

    # Picker lists real databases; pick the seeded one.
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="qvtest"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - qvtest")
    shot("connected to qvtest")

    # Query the seeded table with pagination + order.
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
    shot("postgres results page 1")

    # CSV download of the current page.
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    csv_text = open(dl_info.value.path(), encoding="utf-8").read()
    assert "name" in csv_text and "alpha" in csv_text

    # Next page.
    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
    shot("postgres results page 2")


def test_postgres_fields_describe(seeded_pg_db, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new postgres")
    page.keyboard.press("Enter")
    page.get_by_test_id("pg-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="qvtest"]').click()
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")
    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"]')).to_have_count(2)
    expect(page.locator('[data-testid="field-toggle"][data-col="id"]')).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"][data-col="name"]')).to_be_visible()
    shot("postgres describe fields")
