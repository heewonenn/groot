import { test, expect } from '@playwright/test'

test('ZIP message references and checklist API share the Vite proxy on port 8765', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('status')).toHaveText('API connected')
  const base = '/api/groups/SYNTHETIC/conversations'
  const list = await page.request.get(base)
  expect(list.status()).toBe(200)
  const items = await list.json()
  expect(items).toHaveLength(1)
  const id = items[0].conversation_id
  const detail = await (await page.request.get(`${base}/${id}`)).json()
  expect(detail.transcript).toHaveLength(detail.message_count)
  const index = detail.transcript.findIndex((m: { speaker: string }) => m.speaker === 'operator')
  const message = detail.transcript[index]
  expect(message.message_id).toBe(`${id}:m${String(index + 1).padStart(4, '0')}`)
  const evidence = await page.request.get(`${base}/${id}/messages/${message.message_id}`)
  expect(evidence.status()).toBe(200)
  expect(await evidence.json()).toEqual(message)
  const wrongGroup = await page.request.get(`/api/groups/OTHER/conversations/${id}/messages/${message.message_id}`)
  expect(wrongGroup.status()).toBe(404)
  await page.goto('/checklists?checklist=freedom-chat')
  await page.getByRole('button', { name: 'Проверка расчёта', exact: true }).click()
  await page.getByRole('button', { name: 'Пример 85,38', exact: true }).click()
  await expect(page.getByTestId('calculation-total')).toHaveText('85,38 / 100')
})
