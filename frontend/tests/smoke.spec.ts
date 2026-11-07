import { test, expect } from '@playwright/test'

test('real API connection, keyboard retry, and narrow viewport', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Hackathon AI', exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('API connected')
  await page.getByRole('button', { name: 'Check again' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status')).toHaveText('API connected')
  await page.setViewportSize({ width: 375, height: 812 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(errors).toEqual([])
})

test('API error is visible and retry recovers', async ({ page }) => {
  await page.route('**/api/health', route => route.abort())
  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('API unavailable')
  await page.unroute('**/api/health')
  await page.getByRole('button', { name: 'Check again' }).click()
  await expect(page.getByRole('status')).toHaveText('API connected')
})
