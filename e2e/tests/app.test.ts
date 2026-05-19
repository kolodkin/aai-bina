import { launch } from "@astral/astral"
import { assert, assertEquals, assertStringIncludes } from "@std/assert"

const BASE_URL = Deno.env.get("BASE_URL") ?? "http://localhost:5173"
const SCREENSHOT_DIR = Deno.env.get("SCREENSHOT_DIR") ?? "./screenshots"

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

    await step("shows backend health status", async () => {
      const health = await page.waitForSelector('[data-testid="health-status"]')
      assertStringIncludes(await health.innerText(), "ok")
    })

    await step("lists initial items from the backend", async () => {
      const list = await page.waitForSelector('[data-testid="item-list"]')
      const items = await list.$$("li")
      assert(items.length >= 3, `expected >= 3 items, got ${items.length}`)
      assertStringIncludes(await list.innerText(), "Welcome to QueryView")
    })

    await step("can add a new item", async () => {
      const name = `Test item ${Date.now()}`
      const input = await page.waitForSelector('input[aria-label="New item name"]')
      await input.type(name)

      const button = await page.waitForSelector(
        'button[type="submit"]:not([disabled])',
      )
      await button.click()

      const needle = JSON.stringify(name)
      await page.waitForFunction(
        `Array.from(document.querySelectorAll('[data-testid="item-list"] > li')).some(li => li.textContent && li.textContent.includes(${needle}))`,
      )
    })
  } finally {
    await browser.close()
  }
})
