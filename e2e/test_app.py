import os

from playwright.sync_api import Page, expect

# When "1", assert the ClickHouse connection actually succeeds (CI runs a real
# ClickHouse service). Otherwise only assert the UI flow renders.
EXPECT_CLICKHOUSE_OK = os.environ.get("EXPECT_CLICKHOUSE_OK") == "1"


def test_queryview_e2e(page: Page) -> None:
    # loads the app and shows the heading
    page.goto("/", wait_until="networkidle")
    expect(page.locator("h1")).to_have_text("QueryView")

    # typing `new clickhouse` reveals the connection form
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    for test_id in ("ch-name", "ch-host", "ch-port", "ch-username", "ch-password"):
        expect(page.get_by_test_id(test_id)).to_be_visible()

    # test connection returns a result
    page.get_by_test_id("ch-test").click()
    result = page.get_by_test_id("ch-result")
    expect(result).to_be_visible()
    expect(result).not_to_be_empty()
    if EXPECT_CLICKHOUSE_OK:
        expect(result).to_have_attribute("data-ok", "true")
        expect(result).to_contain_text("Connected")

    # The remaining steps need a real ClickHouse to connect to.
    if not EXPECT_CLICKHOUSE_OK:
        return

    # connect opens the database picker
    page.get_by_test_id("ch-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    expect(page.locator('[data-db="default"]')).to_be_visible()

    # selecting a database shows the connected indicator
    page.locator('[data-db="default"]').click()
    expect(page.get_by_test_id("connection-indicator")).to_be_visible()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - default")

    # reload resumes the session, then reconnect and select the system database
    page.goto("/", wait_until="networkidle")
    # Resume: came back connected to the previously selected database.
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - default")
    # `connect <name>` reopens the picker; choose a different database.
    page.get_by_test_id("prompt-input").fill("connect clickhouse")
    page.keyboard.press("Enter")
    page.locator('[data-db="system"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - system")

    # opening with ?connection=<name> opens that connection
    page.goto("/?connection=clickhouse", wait_until="networkidle")
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="information_schema"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text(
        "connected - information_schema"
    )
