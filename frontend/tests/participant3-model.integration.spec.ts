import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test.skip(process.env.CQA_E2E_MODEL !== '1', 'Requires the explicitly synthetic ModelEvaluator test server.')
const disabled = process.env.CQA_E2E_MODEL_DISABLED === '1'
const group = 'INTEGRATION'
type Metadata = {
  evaluation_id: string; status: string; provider: string; configured_model: string;
  generation_mode: string; estimated_cost_usd: number | null; elapsed_ms: number;
  token_usage: { input_tokens: number; output_tokens: number } | null;
  error_code: string | null; attempts: { error_code: string | null }[];
  input_fingerprint?: string;
}
type Card = {
  evaluation: { evaluation_id: string; checklist_version_id: string; status: string;
    model_result: { criteria: { criterion_id: string; value: unknown }[] };
    original_result: { total: string } | null; final_result: { total: string } | null };
  runtime: { model_run: Metadata | null }; final_status: string; revision: number;
}
type Job = { job_id: string; parent_job_id: string | null; status: string;
  items: { conversation_id: string; evaluation_id: string | null }[] }
type Draft = { checklist_id: string; revision: number; content: {
  channels: string[];
  criteria: { id: string; instruction: string; title: string; weight: number; answer_type: string;
    section_id: string; allow_na: boolean; na_condition: string | null; evidence_required: boolean;
    flag_when: boolean | null; levels: unknown[] }[];
} }

async function open(page: Page, cid: string) {
  await page.goto('/analysis?group_id=' + group + '&conversation_id=' + cid)
  await expect(page.getByRole('heading', { name: 'Анализ разговора.', exact: true })).toBeVisible()
}
async function settled(page: Page, id: string): Promise<Job> {
  let result: Job | undefined
  await expect.poll(async () => {
    const response = await page.request.get('/api/evaluation-jobs/' + id)
    expect(response.status()).toBe(200)
    result = await response.json() as Job
    return result.status
  }, { timeout: 15000 }).not.toMatch(/^(queued|running)$/)
  return result!
}
async function loadCard(page: Page, id: string): Promise<Card> {
  const response = await page.request.get('/api/evaluation-cards/' + id)
  expect(response.status()).toBe(200)
  return response.json()
}
async function launch(page: Page, cid: string, version = 'freedom-chat@1') {
  await open(page, cid)
  await page.getByRole('combobox', { name: 'Версия правил для новой оценки', exact: true }).selectOption(version)
  const receipt = page.waitForResponse(r => r.url().endsWith('/api/evaluation-jobs') && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Оценить диалог', exact: true }).click()
  const response = await receipt
  expect(response.status()).toBe(202)
  const id = (await response.json() as { job_id: string }).job_id
  return settled(page, id)
}
async function readyCard(page: Page, job: Job) {
  const id = job.items[0].evaluation_id!
  expect(id).toBeTruthy()
  await expect(page).toHaveURL(new RegExp('evaluation_id=' + id))
  await expect(page.getByRole('heading', { name: 'Оценка качества', exact: true })).toBeVisible()
  return loadCard(page, id)
}
async function reviewContact(page: Page) {
  await page.getByRole('button', { name: 'Открыть критерий Установление контакта', exact: true }).click()
  await page.getByRole('button', { name: 'Оспорить оценку', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Комментарий к апелляции', { exact: true }).fill('Учебная проверка сохранения исходного ответа после запуска P2.')
  await dialog.getByRole('button', { name: 'Сохранить обращение', exact: true }).click()
  await dialog.getByRole('radio', { name: 'Изменить ответ', exact: true }).check()
  await dialog.getByRole('combobox', { name: 'Новый ответ', exact: true }).selectOption('false')
  await dialog.getByLabel('Обоснование решения', { exact: true }).fill('Контролируемый пересмотр для технической проверки, не реальная экспертная оценка.')
  await dialog.getByRole('button', { name: 'Применить решение', exact: true }).click()
  await expect(dialog).not.toBeVisible()
}

test('model factory disabled state is clear, retryable and never creates an inference job', async ({ page }) => {
  test.skip(!disabled, 'Runs against the separate disabled model factory.')
  const posts: string[] = []
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()) })
  await open(page, 'model-first-001')
  const status = await page.request.get('/api/model-status')
  expect(status.status()).toBe(200)
  expect(await status.json()).toMatchObject({ configured: false, ready: false, reason: 'model_disabled' })
  await expect(page.getByRole('button', { name: 'Оценить диалог', exact: true })).toBeDisabled()
  await expect(page.getByText(/модель.*отключена|модель.*не.*настроена|настройте.*модель/i).first()).toBeVisible()
  await page.getByRole('button', { name: 'Обновить статус модели', exact: true }).focus()
  await page.keyboard.press('Enter')
  expect(posts).toEqual([])
})

test('real evaluator pipeline creates a demo card, exposes exact run metadata, proof and immutable reviewed original', async ({ page }, testInfo) => {
  test.skip(disabled, 'Needs the controlled synthetic provider.')
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  const job = await launch(page, 'model-first-001')
  expect(job.status).toBe('completed')
  const card = await readyCard(page, job)
  const id = card.evaluation.evaluation_id
  expect(card.evaluation.original_result?.total).toBe('85.38')
  expect(card.runtime.model_run).toMatchObject({
    evaluation_id: id, provider: 'synthetic_browser', configured_model: 'synthetic-browser-model',
    generation_mode: 'demo', estimated_cost_usd: 0.0002,
    token_usage: { input_tokens: 100, output_tokens: 50 },
  })
  expect(card.runtime.model_run!.attempts).toHaveLength(1)
  await expect(page.getByText('synthetic-browser-model', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.an-model-audit').getByText(/0[,.]0002/).first()).toBeVisible()
  const proof = page.waitForResponse(r => r.url().includes('/messages/model-first-001'))
  await page.getByRole('button', { name: 'Перейти к фрагменту', exact: true }).click()
  expect((await proof).status()).toBe(200)
  await expect(page.locator('[data-message-id="model-first-001:m0004"]')).toHaveAttribute('aria-current', 'true')
  const quotes = await page.request.get('/api/evaluation-runs/' + id + '/evidence')
  expect(quotes.status()).toBe(200)
  expect(JSON.stringify(await quotes.json())).toContain('Откройте раздел справок')
  await page.getByText('Исходные доказательства AI из сохранённого входа', { exact: true }).click()
  await expect(page.getByRole('button', { name: 'Открыть исходный фрагмент', exact: true }).first()).toBeVisible()
  await reviewContact(page)
  const reviewed = await loadCard(page, id)
  expect(reviewed.evaluation.original_result).toEqual(card.evaluation.original_result)
  expect(reviewed.evaluation.model_result).toEqual(card.evaluation.model_result)
  expect(reviewed.evaluation.final_result?.total).toBe('70.00')
  expect(reviewed.runtime.model_run).toEqual(card.runtime.model_run)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Оценка качества', exact: true })).toBeVisible()
  expect((await loadCard(page, id)).revision).toBe(1)
  const event = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Скачать отчёт карточки', exact: true }).click()
  const path = await (await event).path()
  const report = JSON.parse(await readFile(path!, 'utf8')) as Card
  expect(report.runtime.model_run?.estimated_cost_usd).toBe(0.0002)
  expect(report.evaluation.original_result?.total).toBe('85.38')
  expect(JSON.stringify(report)).not.toContain('audio_url')
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('model-card-desktop.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('failed provider remains visible across reload and retries only when explicitly requested', async ({ page }) => {
  test.skip(disabled, 'Needs the controlled synthetic provider.')
  const cid = 'model-fail-once-001'
  const failed = await launch(page, cid)
  expect(failed.status).toBe('failed')
  expect(failed.items[0].evaluation_id).toBeNull()
  await expect(page.getByRole('button', { name: 'Повторить неуспешные', exact: true })).toBeVisible()
  const history = await page.request.get('/api/evaluation-runs?group_id=' + group + '&conversation_id=' + cid)
  const runs = await history.json() as Metadata[]
  expect(runs).toHaveLength(1)
  expect(runs[0].error_code).toBe('provider_auth')
  const visibleHistory = page.locator('.an-model-history')
  await visibleHistory.locator('summary').click()
  await expect(visibleHistory.getByText('Сервис отклонил серверный ключ или доступ к модели.', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Повторить неуспешные', exact: true })).toBeVisible()
  const receipt = page.waitForResponse(r => r.url().endsWith('/retry') && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Повторить неуспешные', exact: true }).click()
  const response = await receipt
  expect(response.status()).toBe(202)
  const retried = await settled(page, (await response.json() as { job_id: string }).job_id)
  expect(retried.parent_job_id).toBe(failed.job_id)
  expect(retried.status).toBe('completed')
  await readyCard(page, retried)
  const after = await (await page.request.get('/api/evaluation-runs?group_id=' + group + '&conversation_id=' + cid)).json() as Metadata[]
  expect(after).toHaveLength(2)
  expect(after.map(run => run.status).sort()).toEqual(['completed', 'failed'])
})

test('unknown usage is not zero cost, and partial P2 inference keeps its original score unresolved', async ({ page }) => {
  test.skip(disabled, 'Needs the controlled synthetic provider.')
  const unknown = await readyCard(page, await launch(page, 'model-unknown-001'))
  expect(unknown.runtime.model_run?.token_usage).toBeNull()
  expect(unknown.runtime.model_run?.estimated_cost_usd).toBeNull()
  await expect(page.locator('.an-model-audit').getByText(/неизвестна|не.*предоставлен|не.*рассчитан/i).first()).toBeVisible()
  const partial = await readyCard(page, await launch(page, 'model-partial-001'))
  expect(partial.final_status).toBe('partial')
  expect(partial.evaluation.original_result).toBeNull()
  expect(partial.evaluation.final_result).toBeNull()
  expect(partial.runtime.model_run?.status).toBe('partial')
  await expect(page.getByText('Нужна проверка', { exact: true }).first()).toBeVisible()
})

test('concurrent retry conflict discovers the existing child without another provider run', async ({ page }) => {
  test.skip(disabled, 'Needs the controlled synthetic provider.')
  const cid = 'model-concurrent-001'
  const failed = await launch(page, cid)
  expect(failed.status).toBe('failed')
  const retryButton = page.getByRole('button', { name: 'Повторить неуспешные', exact: true })
  await expect(retryButton).toBeVisible()
  // Another reviewer starts the sole permitted retry after this browser stopped polling.
  const createdElsewhere = await page.request.post('/api/evaluation-jobs/' + failed.job_id + '/retry', {
    headers: { 'Idempotency-Key': 'qa-other-reviewer-' + failed.job_id },
  })
  expect(createdElsewhere.status()).toBe(202)
  const child = await settled(page, (await createdElsewhere.json() as { job_id: string }).job_id)
  const uiPosts: string[] = []
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/retry')) uiPosts.push(request.url())
  })
  const conflict = page.waitForResponse(response => response.url().endsWith('/retry') && response.request().method() === 'POST')
  await retryButton.click()
  expect((await conflict).status()).toBe(409)
  await page.getByRole('button', { name: 'Открыть задание повтора', exact: true }).click()
  const saved = await readyCard(page, child)
  expect(saved.evaluation.original_result?.total).toBe('85.38')
  expect(uiPosts).toHaveLength(1)
  const history = await page.request.get('/api/evaluation-runs?group_id=' + group + '&conversation_id=' + cid)
  const runs = await history.json() as Metadata[]
  expect(runs).toHaveLength(2)
  expect(runs.map(run => run.status).sort()).toEqual(['completed', 'failed'])
  await expect(page.locator('.an-launcher .an-error')).toHaveCount(0)
})

test('new and edited immutable rules reach the evaluator and preserve older cards', async ({ page }) => {
  test.skip(disabled, 'Needs the controlled synthetic provider.')
  const created = await page.request.post('/api/checklists', {
    data: { title: 'QA: передача правил P2', copy_from_version_id: 'freedom-chat@1' },
  })
  expect(created.status()).toBe(201)
  let draft = await created.json() as Draft
  const path = '/api/checklists/' + draft.checklist_id
  const publish = async () => {
    const published = await page.request.post(path + '/versions', { data: { expected_revision: draft.revision } })
    expect(published.status()).toBe(200)
    return (await published.json() as { checklist_version_id: string }).checklist_version_id
  }
  const v1 = await publish()
  const original = await readyCard(page, await launch(page, 'model-version-001', v1))
  draft = await (await page.request.get(path + '/draft')).json() as Draft
  draft.content.criteria[0].instruction += ' QA_FALSE — учебный маркер изменения входа.'
  let patched = await page.request.patch(path + '/draft', { data: { expected_revision: draft.revision, content: draft.content } })
  expect(patched.status()).toBe(200)
  draft = await patched.json() as Draft
  const v2 = await publish()
  const edited = await readyCard(page, await launch(page, 'model-version-001', v2))
  expect(edited.evaluation.model_result.criteria.find(c => c.criterion_id === '01')?.value).toBe(false)
  expect(edited.evaluation.original_result?.total).toBe('70.00')
  draft = await (await page.request.get(path + '/draft')).json() as Draft
  draft.content.criteria.push({ ...draft.content.criteria[0], id: '09', title: 'QA: новый критерий', instruction: 'Проверка передачи добавленного правила.', weight: 5 })
  patched = await page.request.patch(path + '/draft', { data: { expected_revision: draft.revision, content: draft.content } })
  expect(patched.status()).toBe(200)
  draft = await patched.json() as Draft
  const v3 = await publish()
  const added = await readyCard(page, await launch(page, 'model-version-001', v3))
  expect(added.evaluation.model_result.criteria).toHaveLength(9)
  expect(added.evaluation.model_result.criteria.find(c => c.criterion_id === '09')?.value).toBe(true)
  const addedAudit = await (await page.request.get('/api/evaluation-runs/' + added.evaluation.evaluation_id)).json() as Metadata
  const editedAudit = await (await page.request.get('/api/evaluation-runs/' + edited.evaluation.evaluation_id)).json() as Metadata
  expect(typeof addedAudit.input_fingerprint).toBe('string')
  expect(addedAudit.input_fingerprint).not.toBe(editedAudit.input_fingerprint)
  expect(await loadCard(page, original.evaluation.evaluation_id)).toEqual(original)
  expect(await loadCard(page, edited.evaluation.evaluation_id)).toEqual(edited)
  // A completed job in the URL must not steal manual navigation to an older card.
  const saved = page.getByRole('combobox', { name: 'Оценка', exact: true })
  await saved.selectOption(original.evaluation.evaluation_id)
  await expect(page).toHaveURL(new RegExp('evaluation_id=' + original.evaluation.evaluation_id))
  expect(new URL(page.url()).searchParams.has('job_id')).toBe(false)
  await page.reload()
  await expect(saved).toHaveValue(original.evaluation.evaluation_id)
  // A draft is editable; it must not hide or alter a compatible published version.
  draft = await (await page.request.get(path + '/draft')).json() as Draft
  draft.content.channels = ['voice call']
  const channelDraft = await page.request.patch(path + '/draft', {
    data: { expected_revision: draft.revision, content: draft.content },
  })
  expect(channelDraft.status()).toBe(200)
  await page.reload()
  await page.getByRole('combobox', { name: 'Версия правил для новой оценки', exact: true }).selectOption(v1)
  await expect(saved).toHaveValue(original.evaluation.evaluation_id)
  await reviewContact(page)
  const reviewedOld = await loadCard(page, original.evaluation.evaluation_id)
  expect(reviewedOld.evaluation.checklist_version_id).toBe(v1)
  expect(reviewedOld.evaluation.original_result?.total).toBe('85.38')
  expect(reviewedOld.evaluation.final_result?.total).toBe('70.00')
  expect(reviewedOld.runtime.model_run).toEqual(original.runtime.model_run)
})

test('model status loading recovers by keyboard and launcher remains usable at 360 pixels', async ({ page }, testInfo) => {
  test.skip(disabled, 'Needs the controlled synthetic provider.')
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/model-status', route => route.abort())
  await open(page, 'model-mobile-001')
  await expect(page.getByRole('alert').first()).toBeVisible()
  await page.unroute('**/api/model-status')
  await page.getByRole('button', { name: 'Обновить статус модели', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: 'Оценить диалог', exact: true })).toBeEnabled()
  await page.setViewportSize({ width: 360, height: 800 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('model-launcher-mobile.png'), fullPage: true })
  expect(errors).toEqual([])
})
