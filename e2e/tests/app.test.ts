import { launch } from "@astral/astral"
import { assert, assertEquals, assertStringIncludes } from "@std/assert"

const BASE_URL = Deno.env.get("BASE_URL") ?? "http://localhost:5173"
const SCREENSHOT_DIR = Deno.env.get("SCREENSHOT_DIR") ?? "./screenshots"
// When "1", assert the ClickHouse connection actually succeeds (CI runs a real
// ClickHouse service). Otherwise only assert the UI flow renders.
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
  // Desktop viewport so screenshots capture the whole page (title, prompt,
  // form, and the top-left connection indicator) instead of a cropped window.
  await page.setViewportSize({ width: 1280, height: 900 })

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

    await step("typing `new clickhouse` reveals the connection form", async () => {
      const input = await page.waitForSelector('[data-testid="prompt-input"]')
      await input.type("new clickhouse")
      await page.keyboard.press("Enter")
      await page.waitForSelector('[data-testid="clickhouse-form"]')
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
        assertEquals(ok, "true", `expected a successful test, got: ${text}`)
        assertStringIncludes(text, "Connected")
      }
    })

    if (EXPECT_CLICKHOUSE_OK) {
      await step("connect opens the database picker", async () => {
        const connect = await page.waitForSelector('[data-testid="ch-connect"]')
        await connect.click()
        await page.waitForSelector('[data-testid="db-picker"]')
        await page.waitForSelector('[data-db="default"]')
      })

      await step("selecting a database shows the connected indicator", async () => {
        const dbButton = await page.waitForSelector('[data-db="default"]')
        await dbButton.click()
        await page.waitForSelector('[data-testid="connection-indicator"]')
        // Acquire the status handle last and read it immediately: an
        // intervening waitForSelector invalidates an element's nodeId in
        // Astral, which makes a later innerText() call throw.
        const status = await page.waitForSelector('[data-testid="connection-status"]')
        assertStringIncludes(await status.innerText(), "connected - default")
      })

      await step("reload resumes the session, then `connect <name> db <database>` switches db", async () => {
        await page.goto(BASE_URL, { waitUntil: "networkidle2" })
        // Resume: came back connected to the previously selected database.
        const resumed = await page.waitForSelector('[data-testid="connection-status"]')
        assertStringIncludes(await resumed.innerText(), "connected - default")
        // Switch databases by name in one command.
        const input = await page.waitForSelector('[data-testid="prompt-input"]')
        await input.type("connect clickhouse db system")
        await page.keyboard.press("Enter")
        await page.waitForFunction(
          `document.querySelector('[data-testid="connection-status"]')?.textContent?.includes('connected - system') === true`,
        )
      })
    }
  } finally {
    await browser.close()
  }
})
