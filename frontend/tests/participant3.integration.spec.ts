import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test.skip(
  process.env.CQA_E2E_PARTICIPANT3 !== '1',
  'Requires the explicitly enabled five-record synthetic participant-3 dataset.',
)

const group = 'SYNTHETIC'
const primary = 'synthetic-contract-001'
const endpoint = (cid: string) => '/api/evaluation-cards/demo-' + cid

type Card = {
  revision: number
  final_status: string
  checklist: { criteria: { id: string; title: string }[] }
  final_answers: { criterion_id: string; value: unknown; applicability: string }[]
  appeals: { appeal_id: string; status: string; comment: string }[]
  evaluation: {
    original_result: { total: string; gross_error: boolean } | null
    final_result: { total: string; gross_error: boolean; applicable_weight: string } | null
    model_result: unknown
    review_history: { evidence: { message_id: string }[] }[]
    status: string
  }
}

async function card(page: Page, cid = primary): Promise<Card> {
  const response = await page.request.get(endpoint(cid))
  expect(response.status()).toBe(200)
  return response.json()
}

async function openCard(page: Page, cid = primary) {
  await page.goto('/analysis?group_id=' + group + '&conversation_id=' + cid + '&evaluation_id=demo-' + cid)
  await expect(page.getByRole('heading', { name: 'Оценка качества', exact: true })).toBeVisible()
}

async function openReview(page: Page, criterion: string, cid = primary) {
  const snapshot = await card(page, cid)
  const title = snapshot.checklist.criteria.find(rule => rule.id === criterion)!.title
  await page.getByRole('button', { name: 'Открыть критерий ' + title, exact: true }).click()
  await page.getByRole('button', { name: 'Оспорить оценку', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Комментарий к апелляции', { exact: true }).fill('Синтетическая проверка интерфейса: пересмотр критерия ' + criterion)
  await dialog.getByRole('button', { name: 'Сохранить обращение', exact: true }).click()
  await expect(dialog.getByRole('heading', { name: 'Решение проверяющего', exact: true })).toBeVisible()
  return dialog
}

async function applyDecision(page: Page, value: string, criterion: string, cid = primary) {
  const dialog = await openReview(page, criterion, cid)
  await dialog.getByRole('radio', { name: 'Изменить ответ', exact: true }).check()
  await dialog.getByRole('combobox', { name: 'Новый ответ', exact: true }).selectOption(value)
  await dialog.getByLabel('Обоснование решения', { exact: true }).fill('Учебный пересмотр по сохранённому правилу и реплике; не разметка DEV.')
  if (value === 'na') {
    await dialog.getByLabel('Почему выполнено условие N/A', { exact: true }).fill('Учебный случай: завершение не требовалось по условию исходного правила.')
  }
  const saved = page.waitForResponse(response => response.url().includes('/decision') && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Применить решение', exact: true }).click()
  expect((await saved).status()).toBe(200)
  await expect(dialog).not.toBeVisible()
  const title = (await card(page, cid)).checklist.criteria.find(rule => rule.id === criterion)!.title
  await expect(page.getByRole('button', { name: 'Открыть критерий ' + title, exact: true }))
    .toHaveAttribute('aria-pressed', 'true')
}

test('review persists original and final scores, separate flag, N/A and audit download through real API', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await openCard(page)
  const original = await card(page)
  expect(original.evaluation.original_result?.total).toBe('85.38')
  await applyDecision(page, '20', '04')
  let reviewed = await card(page)
  expect(reviewed.revision).toBe(1)
  expect(reviewed.evaluation.final_result?.total).toBe('93.08')
  expect(reviewed.evaluation.original_result).toEqual(original.evaluation.original_result)
  expect(reviewed.evaluation.model_result).toEqual(original.evaluation.model_result)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Оценка качества', exact: true })).toBeVisible()
  expect((await card(page)).evaluation.final_result?.total).toBe('93.08')
  const history = page.locator('.an-history')
  await expect(history.getByText('15 → 20', { exact: true })).toBeVisible()
  await expect(history.getByText('Учебный пересмотр по сохранённому правилу и реплике; не разметка DEV.', { exact: true })).toBeVisible()
  await history.getByText('Обращение и доказательства', { exact: true }).click()
  await expect(history.getByText('Синтетическая проверка интерфейса: пересмотр критерия 04', { exact: true })).toBeVisible()
  await expect(history.locator('code')).toContainText(['SYNTHETIC / synthetic-contract-001 / synthetic-contract-001:m0006'])
  await applyDecision(page, 'true', '08')
  reviewed = await card(page)
  expect(reviewed.evaluation.final_result?.gross_error).toBe(true)
  expect(reviewed.evaluation.final_result?.total).toBe('93.08')
  await applyDecision(page, 'na', '07')
  reviewed = await card(page)
  expect(reviewed.evaluation.final_result?.total).toBe('92.50')
  expect(reviewed.evaluation.final_result?.applicable_weight).toBe('60.00')
  expect(reviewed.evaluation.original_result?.total).toBe('85.38')
  expect(reviewed.appeals).toHaveLength(3)
  expect(reviewed.appeals.every(appeal => appeal.status === 'resolved')).toBe(true)
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: /Скачать.*отчёт|Отчёт карточки|Скачать.*аудит/ }).click()
  const download = await downloadEvent
  const filePath = await download.path()
  expect(filePath).not.toBeNull()
  const audit = JSON.parse(await readFile(filePath!, 'utf8')) as Card
  expect(audit.evaluation.original_result?.total).toBe('85.38')
  expect(audit.evaluation.final_result?.total).toBe('92.50')
  expect(audit.appeals).toHaveLength(3)
  expect(JSON.stringify(audit)).not.toContain('audio_url')
  await page.screenshot({ path: testInfo.outputPath('participant3-analysis-desktop.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('partial AI answer can be resolved by a human without manufacturing an original score', async ({ page }) => {
  const cid = 'synthetic-partial-001'
  await openCard(page, cid)
  const before = await card(page, cid)
  expect(before.final_status).toBe('partial')
  expect(before.evaluation.original_result).toBeNull()
  expect(before.evaluation.final_result).toBeNull()
  const dialog = await openReview(page, '01', cid)
  await expect(dialog.getByRole('radio', { name: 'Подтвердить ответ', exact: true })).toBeDisabled()
  await dialog.getByRole('combobox', { name: 'Новый ответ', exact: true }).selectOption('true')
  await dialog.getByLabel('Обоснование решения', { exact: true }).fill('Учебное ручное разрешение исходного неопределённого ответа.')
  await dialog.getByRole('button', { name: 'Применить решение', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText('Не определён → Да', { exact: true })).toBeVisible()
  const after = await card(page, cid)
  expect(after.final_status).toBe('completed')
  expect(after.evaluation.status).toBe('partial')
  expect(after.evaluation.original_result).toBeNull()
  expect(after.evaluation.model_result).toEqual(before.evaluation.model_result)
  expect(after.evaluation.final_result?.total).toBe('85.38')
})

test('official PDF export reports an invalid demo dataset and does not download fake results', async ({ page }) => {
  await openCard(page, 'synthetic-en-001')
  await page.getByRole('button', { name: 'Экспорт для сдачи', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: 'Где фактически выполнялась модель', exact: true }).selectOption('external_api')
  await dialog.getByRole('checkbox').first().check()
  const downloads: string[] = []
  page.on('download', download => downloads.push(download.suggestedFilename()))
  const returned = page.waitForResponse(response => response.url().includes('/api/submissions/prepare'))
  await dialog.getByRole('button', { name: 'Проверить и скачать', exact: true }).click()
  expect((await returned).status()).toBe(422)
  await expect(dialog.getByRole('alert')).toBeVisible()
  expect(downloads).toEqual([])
})

test('card loading error recovers with keyboard retry and narrow screen stays within viewport', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/evaluation-cards/demo-synthetic-kz-001', route => route.abort())
  await page.goto('/analysis?group_id=SYNTHETIC&conversation_id=synthetic-kz-001&evaluation_id=demo-synthetic-kz-001')
  await expect(page.getByRole('alert')).toBeVisible()
  await page.unroute('**/api/evaluation-cards/demo-synthetic-kz-001')
  await page.getByRole('button', { name: /Повторить/ }).first().focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Оценка качества', exact: true })).toBeVisible()
  await expect(page.getByText('Оқу сервисінде анықтаманы қалай жүктеймін?', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 360, height: 800 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('participant3-analysis-mobile.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('message evidence keeps bot and system with equal timestamps distinct and survives reload', async ({ page }) => {
  const cid = 'synthetic-en-001'
  await openCard(page, cid)
  const snapshot = await card(page, cid)
  const first = snapshot.checklist.criteria.find(rule => rule.id === '01')!
  await page.getByRole('button', { name: 'Открыть критерий ' + first.title, exact: true }).click()
  const proof = page.waitForResponse(response => response.url().includes('/messages/') && response.url().endsWith('m0004'))
  await page.getByRole('button', { name: 'Перейти к фрагменту', exact: true }).click()
  expect((await proof).status()).toBe(200)
  await expect(page).toHaveURL(/message_id=synthetic-en-001%3Am0004/)
  const botId = cid + ':m0002'
  const systemId = cid + ':m0003'
  const bot = page.locator('[data-message-id="' + botId + '"]')
  const system = page.locator('[data-message-id="' + systemId + '"]')
  expect(await bot.locator('time').textContent()).toBe(await system.locator('time').textContent())
  await page.getByRole('button', { name: 'Выделить реплику ' + systemId, exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(system).toHaveAttribute('aria-current', 'true')
  await expect(bot).not.toHaveAttribute('aria-current', 'true')
  await expect(page).toHaveURL(/message_id=synthetic-en-001%3Am0003/)
  await page.reload()
  await expect(system).toHaveAttribute('aria-current', 'true')
  await expect(system).toContainText('The operator has joined.')
})

test('call fixture has no fabricated player position or recording', async ({ page }, testInfo) => {
  await openCard(page, 'synthetic-call-001')
  await expect(page.getByText(/Запись.*недоступна|запись.*не|аудио.*нет/i).first()).toBeVisible()
  expect(await page.locator('audio').count()).toBe(0)
  await page.screenshot({ path: testInfo.outputPath('participant3-call-unavailable.png'), fullPage: true })
})
