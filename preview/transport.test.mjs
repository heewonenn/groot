/** Test the shipped static transport. Fixtures are produced by the real Python calculator. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import vm from 'node:vm'
import { webcrypto } from 'node:crypto'

const fixture = JSON.parse(await readFile(new URL('./public/preview-data.json', import.meta.url), 'utf8'))
const source = await readFile(new URL('./public/preview-fetch.js', import.meta.url), 'utf8')

async function boot(storage = new Map()) {
  const sandbox = vm.createContext({
    structuredClone, Request, Response, Headers, URL, crypto: webcrypto,
    location: {href: 'https://preview.example/?view=analysis', origin: 'https://preview.example'},
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    fetch: async url => {
      assert.equal(url, '/preview-data.json', 'Only the public synthetic fixture is loaded')
      return new Response(JSON.stringify(fixture), {status: 200})
    },
  })
  vm.runInContext(source, sandbox)
  await sandbox.__CQA_PREVIEW_BOOT__
  async function request(path, body, key) {
    const response = await sandbox.fetch('https://preview.example' + path, body === undefined ? {} : {
      method: 'POST', body: JSON.stringify(body), headers: key ? {'Idempotency-Key': key} : {},
    })
    return {status: response.status, data: await response.json()}
  }
  return {request, storage, sandbox}
}

const eid = 'demo-synthetic-contract-001'
const reference = (conversation = 'synthetic-contract-001', id = 6) => ({group_id: 'SYNTHETIC',
  conversation_id: conversation, message_id: `${conversation}:m${String(id).padStart(4, '0')}`,
  kind: 'support', span: null})
const change = (value, revision = 0, overrides = {}) => ({action: 'change', value,
  applicability: value === null ? 'not_applicable' : 'applicable', reason: 'Проверено на учебном примере.',
  na_reason: value === null ? 'Учебное обоснование неприменимости.' : null,
  evidence: [reference()], expected_revision: revision, ...overrides})

async function appeal(request, criterion, evaluation = eid) {
  const created = await request(`/api/evaluations/${evaluation}/appeals`, {criterion_id: criterion, comment: 'Учебная апелляция.'})
  assert.equal(created.status, 201)
  return created.data.appeal_id
}

test('native cards, triple evidence identity and same-timestamp messages remain distinct', async () => {
  const {request} = await boot()
  const items = await request('/api/evaluations?group_id=SYNTHETIC')
  assert.equal(items.data.length, 5)
  const bot = await request('/api/groups/SYNTHETIC/conversations/synthetic-contract-001/messages/synthetic-contract-001:m0002')
  const system = await request('/api/groups/SYNTHETIC/conversations/synthetic-contract-001/messages/synthetic-contract-001:m0003')
  assert.equal(bot.data.timestamp, system.data.timestamp)
  assert.notEqual(bot.data.message_id, system.data.message_id)
  assert.equal(bot.data.speaker, 'bot')
  assert.equal(system.data.speaker, 'system')
  const wrong = await request('/api/groups/PRIVATE/conversations')
  assert.equal(wrong.status, 404)
})

test('review uses native precomputed scores, immutable original, N/A and independent flag', async () => {
  const {request, storage} = await boot()
  const first = await appeal(request, '04')
  const body = change(20)
  const saved = await request(`/api/appeals/${first}/decision`, body, 'first')
  assert.equal(saved.status, 200)
  assert.equal(saved.data.evaluation.original_result.total, '85.38')
  assert.equal(saved.data.evaluation.final_result.total, '93.08')
  assert.equal(saved.data.evaluation.model_result.criteria.find(a => a.criterion_id === '04').value, 15)
  const duplicate = await request(`/api/appeals/${first}/decision`, body, 'first')
  assert.deepEqual(duplicate.data, saved.data)
  const reused = await request(`/api/appeals/${first}/decision`, {...body, reason: 'Другой запрос.'}, 'first')
  assert.equal(reused.status, 409)
  const second = await appeal(request, '07')
  assert.equal((await request(`/api/appeals/${second}/decision`, change(null), 'stale')).status, 409)
  const na = await request(`/api/appeals/${second}/decision`, change(null, 1), 'na')
  assert.equal(na.status, 200)
  assert.equal(na.data.evaluation.final_result.total, '92.50')
  assert.equal(na.data.evaluation.final_result.applicable_weight, '60.00')
  const flag = await appeal(request, '08')
  const flagged = await request(`/api/appeals/${flag}/decision`, change(true, 2), 'flag')
  assert.equal(flagged.data.evaluation.final_result.total, '92.50')
  assert.equal(flagged.data.evaluation.final_result.gross_error, true)
  const afterReload = await boot(storage)
  const persisted = await afterReload.request(`/api/evaluation-cards/${eid}`)
  assert.deepEqual(persisted.data, flagged.data)
  const exactOldReceipt = await afterReload.request(`/api/appeals/${first}/decision`, body, 'first')
  assert.deepEqual(exactOldReceipt.data, saved.data, 'Replay returns its saved response before revision validation')
})

test('partial AI result stays partial while a human can complete the final answers', async () => {
  const {request} = await boot()
  const id = 'demo-synthetic-partial-001'
  const opened = await appeal(request, '01', id)
  const saved = await request(`/api/appeals/${opened}/decision`, change(true, 0,
    {evidence: [reference('synthetic-partial-001', 4)]}), 'partial')
  assert.equal(saved.status, 200)
  assert.equal(saved.data.final_status, 'completed')
  assert.equal(saved.data.evaluation.status, 'partial')
  assert.equal(saved.data.evaluation.original_result, null)
  assert.equal(saved.data.evaluation.final_result.total, '85.38')
  assert.equal(saved.data.evaluation.model_result.criteria[0].decision_status, 'unresolved')
})

test('invalid boolean, N/A, range and confirmation cannot change stored state', async () => {
  const {request} = await boot()
  const opened = await appeal(request, '01')
  const path = `/api/appeals/${opened}/decision`
  for (const body of [change(1), change(null), change(false, 0, {reason: ' '}),
    change(false, 0, {action: 'confirm'}), change(false, 0, {evidence: [reference('synthetic-call-001')]}),
    change(false, 0, {evidence: [{...reference(), span: {start_message_id: 'synthetic-contract-001:m0006',
      end_message_id: 'synthetic-contract-001:m0002'}}]})]) {
    assert.equal((await request(path, body, webcrypto.randomUUID())).status, 422)
  }
  const card = await request(`/api/evaluation-cards/${eid}`)
  assert.equal(card.data.revision, 0)
  assert.equal(card.data.appeals[0].status, 'open')
  assert.equal(card.data.evaluation.final_result.total, '85.38')
})

test('preview refuses AI generation and official submission, exposes separate audit', async () => {
  const {request} = await boot()
  assert.equal((await request('/api/capabilities')).data.model_available, false)
  assert.equal((await request('/api/evaluation-jobs', {group_id: 'SYNTHETIC'})).status, 503)
  assert.equal((await request('/api/submissions/prepare', {group_id: 'SYNTHETIC'})).status, 422)
  const audit = await request(`/api/evaluation-cards/${eid}/audit`)
  assert.equal(audit.status, 200)
  assert.equal(audit.data.evaluation.generation.mode, 'demo')
  assert.equal('transcript' in audit.data, false)
})

test('real Axios fetch adapter sends Request JSON and Idempotency-Key through the preview', async () => {
  const {sandbox} = await boot()
  const previousFetch = globalThis.fetch
  const seen = []
  globalThis.fetch = async (input, init) => {
    seen.push({isRequest: input instanceof Request, method: input.method,
      key: input.headers?.get('Idempotency-Key')})
    return sandbox.fetch(input, init)
  }
  const {default: axios} = await import('../frontend/node_modules/axios/index.js')
  const previousAdapter = axios.defaults.adapter
  axios.defaults.adapter = 'fetch'
  const client = axios.create({baseURL: 'https://preview.example', timeout: 5000})
  try {
    const created = await client.post(`/api/evaluations/${eid}/appeals`,
      {criterion_id: '04', comment: 'Проверка настоящего Axios.'})
    assert.equal(created.status, 201)
    const duplicate = await client.post(`/api/evaluations/${eid}/appeals`,
      {criterion_id: '04', comment: 'Повтор открытого обращения.'})
    assert.equal(duplicate.status, 201)
    assert.equal(duplicate.data.appeal_id, created.data.appeal_id)
    const path = `/api/appeals/${created.data.appeal_id}/decision`
    const saved = await client.post(path, change(20), {headers: {'Idempotency-Key': 'axios-fetch'}})
    assert.equal(saved.data.evaluation.final_result.total, '93.08')
    const repeated = await client.post(path, change(20), {headers: {'Idempotency-Key': 'axios-fetch'}})
    assert.deepEqual(repeated.data, saved.data)
    assert.ok(seen.every(request => request.isRequest), 'Axios uses actual Request objects')
    assert.equal(seen[2].method, 'POST')
    assert.equal(seen[2].key, 'axios-fetch')
  } finally {
    globalThis.fetch = previousFetch
    axios.defaults.adapter = previousAdapter
  }
})

test('checklist reads use native participant-1 serialized objects and writes are refused', async () => {
  const {request, sandbox} = await boot()
  assert.deepEqual((await request('/api/checklists')).data, fixture.checklist_summaries)
  for (const version of fixture.checklists) {
    const versions = await request(`/api/checklists/${version.checklist_id}/versions`)
    assert.deepEqual(versions.data, [version])
    const draft = await request(`/api/checklists/${version.checklist_id}/draft`)
    assert.deepEqual(draft.data, fixture.checklist_drafts[version.checklist_id])
    assert.equal(draft.data.base_version_id, version.checklist_version_id)
    const response = await sandbox.fetch(`https://preview.example/api/checklists/${version.checklist_id}/draft`,
      {method: 'PATCH', body: JSON.stringify({expected_revision: 1, content: draft.data.content})})
    assert.equal(response.status, 405)
  }
})
