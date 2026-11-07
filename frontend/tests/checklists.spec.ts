import { test, expect } from '@playwright/test'

test('editor publishes immutable versions and restores rules after reload', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/checklists?checklist=freedom-chat')
  await expect(page.getByLabel('Правило критерия 01')).toBeVisible()
  await page.screenshot({ path: 'test-results/checklists-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Копия', exact: true }).click()
  const name = `Тест версий ${Date.now()}`
  await page.getByLabel('Название копии', { exact: true }).fill(name)
  await page.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect(page.getByLabel('Название чек-листа', { exact: true })).toHaveValue(name)
  await page.getByRole('button', { name: 'Сохранить версию', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Сохранена версия 1')
  const original = await page.getByLabel('Правило критерия 01').inputValue()
  const changed = 'Оператор поздоровался на языке клиента. Представляться не требуется.'
  await page.getByLabel('Правило критерия 01').fill(changed)
  await page.getByText('Управление диалогом, скорость и грамотность', { exact: true }).click()
  await page.getByLabel('Значение ступени 3 критерия 06').fill('8')
  await page.getByLabel('Описание ступени 3 критерия 06').fill('Один небольшой сбой в объяснении.')
  await page.getByRole('button', { name: 'Сохранить версию', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Сохранена версия 2')
  const id = new URL(page.url()).searchParams.get('checklist')!
  await page.getByLabel('Версия чек-листа').selectOption(`${id}@1`)
  await expect(page.getByLabel('Правило критерия 01')).toHaveValue(original)
  await expect(page.getByLabel('Правило критерия 01')).toBeDisabled()
  await expect(page.getByLabel('Название чек-листа', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Копия', exact: true }).click()
  await page.getByLabel('Название копии', { exact: true }).fill(`${name} — из v1`)
  await page.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect(page.getByLabel('Название чек-листа', { exact: true })).toHaveValue(`${name} — из v1`)
  await expect(page.getByLabel('Правило критерия 01')).toHaveValue(original)
  await page.goto(`/checklists?checklist=${id}`)
  await page.reload()
  await expect(page.getByLabel('Название чек-листа', { exact: true })).toHaveValue(name)
  await expect(page.getByLabel('Правило критерия 01')).toHaveValue(changed)
  const versions = await (await page.request.get(`/api/checklists/${id}/versions`)).json()
  expect(versions).toHaveLength(2)
  expect(versions[0].criteria[5].levels[2].value).toBe(8)
  expect(versions[1].criteria[5].levels[2].value).toBe(7)
  expect(errors).toEqual([])
})

test('shared arithmetic, N/A and separate gross flag work through the real API', async ({ page }) => {
  await page.goto('/checklists?checklist=freedom-chat')
  await page.getByRole('button', { name: 'Проверка расчёта', exact: true }).click()
  await page.getByRole('button', { name: 'Пример 85,38', exact: true }).click()
  await expect(page.getByTestId('calculation-total')).toHaveText('85,38 / 100')
  await page.getByRole('button', { name: 'После пересмотра 93,08', exact: true }).click()
  await expect(page.getByTestId('calculation-total')).toHaveText('93,08 / 100')
  await page.getByRole('button', { name: 'Пример 85,38', exact: true }).click()
  await expect(page.getByTestId('calculation-total')).toHaveText('85,38 / 100')
  await page.getByLabel('Ответ по критерию 02').selectOption('true')
  await page.getByRole('button', { name: 'Рассчитать итог', exact: true }).click()
  await expect(page.getByTestId('calculation-total')).toHaveText('88,13 / 100')
  await page.getByLabel('Ответ по критерию 08').selectOption('true')
  await page.getByRole('button', { name: 'Рассчитать итог', exact: true }).click()
  await expect(page.getByTestId('calculation-total')).toHaveText('88,13 / 100')
  await expect(page.getByText('Грубая ошибка: да', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Ответ по критерию 06').locator('option[value="na"]')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/checklists-calculation.png', fullPage: true })
  await page.setViewportSize({ width: 360, height: 800 })
  await expect(page.getByRole('button', { name: 'Рассчитать итог', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/checklists-mobile.png', fullPage: true })
})

test('new criterion validates N/A before publishing and supports keyboard creation', async ({ page }) => {
  await page.goto('/checklists?checklist=freedom-chat')
  await expect(page.getByLabel('Правило критерия 01')).toBeVisible()
  await page.getByRole('button', { name: 'Новый чек-лист', exact: true }).focus()
  await page.keyboard.press('Enter')
  const name = `Новый набор ${Date.now()}`
  await page.getByLabel('Название нового чек-листа', { exact: true }).fill(name)
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Название чек-листа', { exact: true })).toHaveValue(name)
  await page.getByRole('button', { name: 'Добавить критерий', exact: true }).click()
  await page.getByLabel(/^Название критерия /).fill('Уточнение следующего шага')
  await page.getByLabel(/^Правило критерия /).fill('Оператор объяснил следующий шаг клиенту.')
  await page.getByLabel('Разрешить N/A — критерий неприменим', { exact: true }).check()
  await page.getByRole('button', { name: 'Сохранить версию', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('когда допустим N/A')
  await page.getByLabel(/^Условие N\/A /).fill('Следующий шаг не требовался.')
  await page.getByRole('button', { name: 'Сохранить версию', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Сохранена версия 1')
  await page.reload()
  await expect(page.getByLabel(/^Название критерия /)).toHaveValue('Уточнение следующего шага')
})

test('loading error recovers without substituting demo rules', async ({ page }) => {
  await page.route('**/api/checklists', route => route.abort())
  await page.goto('/checklists')
  await expect(page.getByRole('alert')).toContainText('Не удалось связаться с API')
  await page.unroute('**/api/checklists')
  await page.getByRole('button', { name: 'Повторить загрузку', exact: true }).click()
  await expect(page.getByLabel('Правило критерия 01')).toBeVisible()
})

for (const scenario of [
  { name: 'review value is absent', middle: [] },
  { name: 'review value has a different contribution', middle: [{ value: 20, description: 'Частичный ответ', normalized_value: '0.8' }] },
]) {
  test(`demo buttons require both answer sets: ${scenario.name}`, async ({ page }) => {
    const created = await page.request.post('/api/checklists', {
      data: { title: `Изменённая шкала: ${scenario.name}`, copy_from_version_id: 'freedom-chat@1' },
    })
    expect(created.status()).toBe(201)
    const draft = await created.json()
    draft.content.criteria[3].levels = [
      { value: 0, description: 'Нет ответа', normalized_value: '0' },
      { value: 15, description: 'Основная часть ответа', normalized_value: '0.75' },
      ...scenario.middle,
      { value: 30, description: 'Полный ответ', normalized_value: '1' },
    ]
    const saved = await page.request.patch(`/api/checklists/${draft.checklist_id}/draft`, {
      data: { expected_revision: draft.revision, content: draft.content },
    })
    expect(saved.ok()).toBe(true)
    const published = await page.request.post(`/api/checklists/${draft.checklist_id}/versions`, {
      data: { expected_revision: (await saved.json()).revision },
    })
    expect(published.ok()).toBe(true)
    await page.goto(`/checklists?checklist=${draft.checklist_id}`)
    await page.getByRole('button', { name: 'Проверка расчёта', exact: true }).click()
    await expect(page.getByLabel('Ответ по критерию 04')).toHaveValue('30')
    await expect(page.getByRole('button', { name: 'После пересмотра 93,08', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Пример 85,38', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Рассчитать итог', exact: true }).click()
    await expect(page.getByTestId('calculation-total')).toHaveText('100,00 / 100')
    await expect(page.getByRole('alert')).toHaveCount(0)
  })
}
