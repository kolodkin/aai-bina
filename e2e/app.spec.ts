import { expect, test } from "@playwright/test"

// When "1", assert the ClickHouse connection actually succeeds (CI runs a real
// ClickHouse service). Otherwise only assert the UI flow renders.
const EXPECT_CLICKHOUSE_OK = process.env.EXPECT_CLICKHOUSE_OK === "1"

test("queryview e2e", async ({ page }) => {
  await test.step("loads the app and shows the heading", async () => {
    await page.goto("/", { waitUntil: "networkidle" })
    await expect(page.locator("h1")).toHaveText("QueryView")
  })

  await test.step("typing `new clickhouse` reveals the connection form", async () => {
    await page.getByTestId("prompt-input").fill("new clickhouse")
    await page.keyboard.press("Enter")
    await expect(page.getByTestId("clickhouse-form")).toBeVisible()
    for (const id of ["ch-name", "ch-host", "ch-port", "ch-username", "ch-password"]) {
      await expect(page.getByTestId(id)).toBeVisible()
    }
  })

  await test.step("test connection returns a result", async () => {
    await page.getByTestId("ch-test").click()
    const result = page.getByTestId("ch-result")
    await expect(result).toBeVisible()
    await expect(result).not.toBeEmpty()
    if (EXPECT_CLICKHOUSE_OK) {
      await expect(result).toHaveAttribute("data-ok", "true")
      await expect(result).toContainText("Connected")
    }
  })

  if (!EXPECT_CLICKHOUSE_OK) return

  await test.step("connect opens the database picker", async () => {
    await page.getByTestId("ch-connect").click()
    await expect(page.getByTestId("db-picker")).toBeVisible()
    await expect(page.locator('[data-db="default"]')).toBeVisible()
  })

  await test.step("selecting a database shows the connected indicator", async () => {
    await page.locator('[data-db="default"]').click()
    await expect(page.getByTestId("connection-indicator")).toBeVisible()
    await expect(page.getByTestId("connection-status")).toContainText("connected - default")
  })

  await test.step(
    "reload resumes the session, then reconnect and select the system database",
    async () => {
      await page.goto("/", { waitUntil: "networkidle" })
      // Resume: came back connected to the previously selected database.
      await expect(page.getByTestId("connection-status")).toContainText("connected - default")
      // `connect <name>` reopens the picker; choose a different database.
      await page.getByTestId("prompt-input").fill("connect clickhouse")
      await page.keyboard.press("Enter")
      await page.locator('[data-db="system"]').click()
      await expect(page.getByTestId("connection-status")).toContainText("connected - system")
    },
  )

  await test.step("opening with ?connection=<name> opens that connection", async () => {
    await page.goto("/?connection=clickhouse", { waitUntil: "networkidle" })
    await expect(page.getByTestId("db-picker")).toBeVisible()
    await page.locator('[data-db="information_schema"]').click()
    await expect(page.getByTestId("connection-status")).toContainText(
      "connected - information_schema",
    )
  })
})
