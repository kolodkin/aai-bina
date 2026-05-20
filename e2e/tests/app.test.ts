import { launch } from "@astral/astral"
import { assert, assertEquals, assertStringIncludes } from "@std/assert"

const BASE_URL = Deno.env.get("BASE_URL") ?? "http://localhost:5173"
const SCREENSHOT_DIR = Deno.env.get("SCREENSHOT_DIR") ?? "./screenshots"
// When "1", assert the ClickHouse connection test actually succeeds (CI runs a
// real ClickHouse service). Otherwise only assert the UI flow renders a result.
const EXPECT_CLICKHOUSE_OK = Deno.env.get("EXPECT_CLICKHOUSE_OK") === "1"

await Deno.mkdir(SCREENSHOT_DIR, { recursive: true })

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")

Deno.test("queryview e2e", async (t) => {
  const browser = await launch({
    path: Deno.env.get("CHROME_PATH") ?? undefined,
    args: ["--no-sandbox"],
  })
  const page = await browser.newPage()

  let stepIndex = 0
  const step = (name: string, fn: () => Promise<void>) =>
    t.step(name, async () => {
      stepIndex++
      try {
        await fn()
      } finally {
        try {
          const bytes = await page.screenshot()
          const file = `${SCREENSHOT_DIR}/${
            String(stepIndex).padStart(2, "0")
          }-${slug(name)}.png`
          await Deno.writeFile(file, bytes)
        } catch (err) {
          console.error(`screenshot failed for "${name}":`, err)
        }
      }
    })

  try {
    await step("loads the app and shows the heading", async () => {
      await page.goto(BASE_URL, { waitUntil: "networkidle2" })
      const h1 = await page.waitForSelector("h1")
      assertEquals(await h1.innerText(), "QueryView")
    })

    await step("typing `connect clickhouse` reveals the connection form", async () => {
      const input = await page.waitForSelector('[data-testid="prompt-input"]')
      await input.type("connect clickhouse")
      await page.keyboard.press("Enter")
      await page.waitForSelector('[data-testid="clickhouse-form"]')
    })

    await step("shows host/port/username/password fields with defaults", async () => {
      for (const id of ["ch-name", "ch-host", "ch-port", "ch-username", "ch-password"]) {
        await page.waitForSelector(`[data-testid="${id}"]`)
      }
    })

    await step("test connection returns a result", async () => {
      const button = await page.waitForSelector('[data-testid="ch-test"]')
      await button.click()
      const result = await page.waitForSelector('[data-testid="ch-result"]')
      const text = await result.innerText()
      assert(text.trim().length > 0, "expected a non-empty result message")

      if (EXPECT_CLICKHOUSE_OK) {
        const ok = await result.getAttribute("data-ok")
        assertEquals(ok, "true", `expected a successful connection, got: ${text}`)
        assertStringIncludes(text, "Connected")
      }
    })

    if (EXPECT_CLICKHOUSE_OK) {
      await step("shows the connected indicator in the top-left", async () => {
        const status = await page.waitForSelector('[data-testid="connection-status"]')
        await page.waitForSelector('[data-testid="connection-indicator"]')
        assertStringIncludes(await status.innerText(), "clickhouse")
      })
    }
  } finally {
    await browser.close()
  }
})
