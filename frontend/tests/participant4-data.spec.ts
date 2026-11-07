import { expect, test } from '@playwright/test'

const cid = 'synthetic-contract-001'
const group = 'SYNTHETIC'

test('opens real team-v1 evidence with duplicate timestamps and preserves it on reload', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Открыть диалоги' }).click()
  await page.getByLabel('Набор', { exact: true }).fill(group)
  await page.getByLabel('Язык', { exact: true }).selectOption('RU')
  await page.getByLabel('Канал', { exact: true }).selectOption('agent chat')
  await page.getByRole('button', { name: 'Показать', exact: true }).click()
  await page.getByRole('link', { name: new RegExp(cid) }).click()
  const systemMessage = cid + ':m0003'
  const link = page.getByRole('link', { name: 'Ссылка на реплику ' + systemMessage })
  await link.focus()
  await page.keyboard.press('Enter')
  const selected = page.locator('li.cv-turn[aria-current="true"]')
  await expect(selected).toContainText('Оператор подключён.')
  await expect(selected).toBeFocused()
  await expect(page).toHaveURL(/message_id=synthetic-contract-001%3Am0003/)
  expect(Object.fromEntries(new URL(page.url()).searchParams)).toMatchObject({
    group_id: group, conversation_id: cid, language: 'RU', channel: 'agent chat',
  })
  await page.reload()
  await expect(selected).toContainText('Оператор подключён.')
  await page.screenshot({ path: 'test-results/participant4-transcript-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 360, height: 800 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/participant4-transcript-mobile.png', fullPage: true })
  await page.getByLabel('Язык', { exact: true }).selectOption('EN')
  await page.getByRole('button', { name: 'Показать', exact: true }).click()
  await expect(page.getByText('По этим фильтрам диалогов нет.')).toBeVisible()
})

test('handles unavailable group, lists recover after retry, and filters persist', async ({ page }) => {
  await page.route('**/api/groups/SYNTHETIC/conversations?*', route => route.abort())
  await page.goto('/conversations?group_id=SYNTHETIC&language=RU')
  await expect(page.getByRole('alert')).toBeVisible()
  await page.unroute('**/api/groups/SYNTHETIC/conversations?*')
  await page.getByRole('button', { name: 'Повторить запрос' }).click()
  await expect(page.getByRole('link', { name: new RegExp(cid) })).toBeVisible()
  await expect(page.getByLabel('Язык', { exact: true })).toHaveValue('RU')
})

test('import displays the returned count and failed imports leave the previous list accessible', async ({ page }) => {
  const path = '**/api/groups/SYNTHETIC/import'
  await page.goto('/conversations?group_id=SYNTHETIC')
  await page.route(path, route => route.fulfill({ json: { group_id: group, total: 1, inserted: 0, unchanged: 1 } }))
  await page.getByRole('button', { name: 'Импортировать набор' }).click()
  await expect(page.getByText('Диалогов: 1. Добавлено: 0.', { exact: true })).toBeVisible()
  await page.unroute(path)
  await page.route(path, route => route.fulfill({ status: 409, json: { detail: 'Группа не обновлена.' } }))
  await page.getByRole('button', { name: 'Импортировать набор' }).click()
  await expect(page.getByRole('alert')).toHaveText('Группа не обновлена.')
  await expect(page.getByRole('link', { name: new RegExp(cid) })).toBeVisible()
})

function silence() {
  const sampleRate = 8000
  const dataSize = sampleRate * 2 * 8
  const wav = Buffer.alloc(44 + dataSize)
  wav.write('RIFF'); wav.writeUInt32LE(36 + dataSize, 4); wav.write('WAVEfmt ', 8)
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22)
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28)
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(dataSize, 40)
  return wav
}

test('synthetic recording seeks to an explicit verified anchor and handles missing media', async ({ page }) => {
  const call = {
    group_id: 'PLAYER', conversation_id: 'call-1', channel: 'voice call', language: 'RU',
    message_count: 2, has_audio: true,
  }
  const message = { message_id: 'call-1:m0001', speaker: 'operator', timestamp: '2026-11-07T10:00:00Z', text: 'Тестовая фраза.' }
  const previous = { ...message, message_id: 'call-1:m0002', speaker: 'client', text: 'Спасибо.' }
  const path = '**/api/groups/PLAYER/conversations'
  await page.route(path + '*', route => route.fulfill({ json: [call] }))
  await page.route(path + '/call-1', route => route.fulfill({ json: { ...call, transcript: [message, previous] } }))
  await page.route(path + '/call-1/messages/*', route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!)
    return route.fulfill({ json: id === message.message_id ? message : previous })
  })
  await page.route(path + '/call-1/audio-anchors', route => route.fulfill({ json: {
    status: 'verified_fragments', anchors: [
      { message_id: message.message_id, start_seconds: 3.25, end_seconds: 4.7 },
      { message_id: previous.message_id, start_seconds: 5.1, end_seconds: 6.2 },
    ],
  } }))
  await page.route(path + '/call-1/audio', route => {
    const recording = silence()
    const match = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/)
    // Match the real audio proxy: Chromium cannot seek in a stream that ignores Range.
    if (match) {
      const start = Number(match[1])
      const end = match[2] ? Math.min(Number(match[2]), recording.length - 1) : recording.length - 1
      if (start > end || start >= recording.length) {
        return route.fulfill({ status: 416, headers: { 'Content-Range': `bytes */${recording.length}` } })
      }
      const body = recording.subarray(start, end + 1)
      return route.fulfill({
        status: 206, contentType: 'audio/wav', body,
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${recording.length}`, 'Content-Length': String(body.length) },
      })
    }
    return route.fulfill({
      contentType: 'audio/wav', body: recording,
      headers: { 'Accept-Ranges': 'bytes', 'Content-Length': String(recording.length) },
    })
  })
  await page.goto('/conversations?group_id=PLAYER&conversation_id=call-1&message_id=call-1%3Am0002&channel=voice+call&language=RU')
  await expect(page.locator('li.cv-turn[aria-current="true"]')).toContainText(previous.text)
  const first = page.getByRole('button', { name: /Перейти к фрагменту .*m0001/ })
  await first.click()
  await expect(page).toHaveURL(/message_id=call-1%3Am0001/)
  await expect(page.getByText('Позиция выбранной реплики: 0:03.')).toBeVisible()
  expect(new URL(page.url()).searchParams.get('channel')).toBe('voice call')
  await expect(page.locator('li.cv-turn[aria-current="true"]')).toContainText(message.text)
  await expect.poll(() => page.locator('audio').evaluate(el => (el as HTMLAudioElement).currentTime)).toBeGreaterThanOrEqual(3.25)
  await page.unroute(path + '/call-1/audio')
  await page.route(path + '/call-1/audio', route => route.fulfill({ status: 502, body: 'offline' }))
  await page.reload()
  await expect(page.locator('li.cv-turn[aria-current="true"]')).toContainText(message.text)
  await expect(page.getByText('Позиция выбранной реплики: 0:03.')).toBeVisible()
  await page.goBack()
  await expect(page.locator('li.cv-turn[aria-current="true"]')).toContainText(previous.text)
  await first.click()
  await expect(page.getByRole('alert')).toContainText('Запись не загрузилась')
  await expect(page.getByText('Тестовая фраза.')).toBeVisible()
})
