import { expect, type Page, test } from "@playwright/test"
import { mkdir } from "node:fs/promises"

const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR ?? "./screenshots"
// When "1", assert the ClickHouse connection actually succeeds (CI runs a real
// ClickHouse service). Otherwise only assert the UI flow renders.
const EXPECT_CLICKHOUSE_OK = process.env.EXPECT_CLICKHOUSE_OK === "1"

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")

test("queryview e2e", async ({ page }) => {
  await mkdir(SCREENSHOT_DIR, { recursive: true })

  // Capture a screenshot after every step (pass or fail) into NN-slug.png so
  // the report builder / e2e-report skill can render the run as a gallery.
  let stepIndex = 0
  const step = (name: string, fn: () => Promise<void>) =>
    test.step(name, async () => {
      stepIndex++
      try {
        await fn()
      } finally {
        const file = `${SCREENSHOT_DIR}/${
          String(stepIndex).padStart(2, "0")
        }-${slug(name)}.png`
        await page.screenshot({ path: file }).catch((err) =>
          console.error(`screenshot failed for "${name}":`, err)
        )
      }
    })

  await step("loads the app and shows the heading", async () => {
    await page.goto("/", { waitUntil: "networkidle" })
    await expect(page.locator("h1")).toHaveText("QueryView")
  })

  await step("typing `new clickhouse` reveals the connection form", async () => {
    await page.getByTestId("prompt-input").fill("new clickhouse")
    await page.keyboard.press("Enter")
    await expect(page.getByTestId("clickhouse-form")).toBeVisible()
    for (const id of ["ch-name", "ch-host", "ch-port", "ch-username", "ch-password"]) {
      await expect(page.getByTestId(id)).toBeVisible()
    }
  })

  await step("test connection returns a result", async () => {
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

  await step("connect opens the database picker", async () => {
    await page.getByTestId("ch-connect").click()
    await expect(page.getByTestId("db-picker")).toBeVisible()
    await expect(page.locator('[data-db="default"]')).toBeVisible()
  })

  await step("selecting a database shows the connected indicator", async () => {
    await page.locator('[data-db="default"]').click()
    await expect(page.getByTestId("connection-indicator")).toBeVisible()
    await expect(page.getByTestId("connection-status")).toContainText("connected - default")
  })

  await step(
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

  await step("opening with ?connection=<name> opens that connection", async () => {
    await page.goto("/?connection=clickhouse", { waitUntil: "networkidle" })
    await expect(page.getByTestId("db-picker")).toBeVisible()
    await page.locator('[data-db="information_schema"]').click()
    await expect(page.getByTestId("connection-status")).toContainText(
      "connected - information_schema",
    )
  })
})
