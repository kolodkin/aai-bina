import { launch } from "@astral/astral"
import { assert, assertEquals, assertStringIncludes } from "@std/assert"

const BASE_URL = Deno.env.get("BASE_URL") ?? "http://localhost:5173"

Deno.test("queryview e2e", async (t) => {
  const browser = await launch({
    path: Deno.env.get("CHROME_PATH") ?? undefined,
    args: ["--no-sandbox"],
  })
  const page = await browser.newPage()

  await t.step("loads the app and shows the heading", async () => {
    await page.goto(BASE_URL, { waitUntil: "networkidle2" })
    const h1 = await page.waitForSelector("h1")
    assertEquals(await h1.innerText(), "QueryView")
  })

  await t.step("shows backend health status", async () => {
    const health = await page.waitForSelector('[data-testid="health-status"]')
    assertStringIncludes(await health.innerText(), "ok")
  })

  await t.step("lists initial items from the backend", async () => {
    const list = await page.waitForSelector('[data-testid="item-list"]')
    const items = await list.$$("li")
    assert(items.length >= 3, `expected >= 3 items, got ${items.length}`)
    assertStringIncludes(await list.innerText(), "Welcome to QueryView")
  })

  await t.step("can add a new item", async () => {
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

  await browser.close()
})
