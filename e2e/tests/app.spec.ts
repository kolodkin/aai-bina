import { test, expect } from '@playwright/test'

test('loads the app and shows the heading', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'QueryView' })).toBeVisible()
})

test('shows backend health status', async ({ page }) => {
  await page.goto('/')
  const health = page.getByTestId('health-status')
  await expect(health).toBeVisible()
  await expect(health).toContainText('ok')
})

test('lists initial items from the backend', async ({ page }) => {
  await page.goto('/')
  const list = page.getByTestId('item-list')
  await expect(list).toBeVisible()
  await expect(list.getByRole('listitem')).toHaveCount(3)
  await expect(list).toContainText('Welcome to QueryView')
})

test('can add a new item', async ({ page }) => {
  await page.goto('/')
  const list = page.getByTestId('item-list')
  const initialCount = await list.getByRole('listitem').count()

  const name = `Test item ${Date.now()}`
  await page.getByLabel('New item name').fill(name)
  await page.getByRole('button', { name: 'Add' }).click()

  await expect(list.getByRole('listitem')).toHaveCount(initialCount + 1)
  await expect(list).toContainText(name)
})
