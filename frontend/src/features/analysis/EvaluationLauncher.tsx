import axios from 'axios'
import { useEffect, useRef, useState } from 'react'
import { Play, RefreshCw } from 'lucide-react'
import type { Summary } from '../dialogues/data'
import { analysisApi, enc, explain, freshKey } from './api'
import type { Checklist } from './types'
import type { ModelStatus } from './modelTypes'
import { modelErrorText, modelRunStatus } from './modelErrors'
import ModelRunHistory from './ModelRunHistory'

interface ChecklistSummary { checklist_id: string; title: string; channels: string[] }
interface Item {
  conversation_id: string; status: string; evaluation_id: string | null; error_code: string | null
}
interface Job {
  job_id: string; group_id: string; checklist_version_id: string; status: string;
  retry_job_id: string | null; total: number; counts: Record<string, number>; items: Item[]
}
interface Receipt { key: string; version: string }
const active = (status: string) => status === 'queued' || status === 'running'
const terminal = (status: string) => ['completed', 'partial', 'failed', 'interrupted'].includes(status)
const storageKey = (group: string, conversation: string) => 'cqa-job-request:' + JSON.stringify([group, conversation])
function readReceipt(key: string): Receipt | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    if (value && typeof value === 'object' && 'key' in value && typeof value.key === 'string'
      && 'version' in value && typeof value.version === 'string') return { key: value.key, version: value.version }
  } catch { /* The current page can still retry safely in memory. */ }
  return null
}
function saveReceipt(key: string, value: Receipt | null) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
  } catch { /* Browser storage is optional for launching the actual server job. */ }
}

export default function EvaluationLauncher({ group, conversation, onCompleted }: {
  group: string; conversation: Summary; onCompleted: (evaluationId: string) => void
}) {
  const savedKey = storageKey(group, conversation.conversation_id)
  const receipt = useRef<Receipt | null>(readReceipt(savedKey))
  const [status, setStatus] = useState<ModelStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [statusMissing, setStatusMissing] = useState(false)
  const [statusAttempt, setStatusAttempt] = useState(0)
  const [versions, setVersions] = useState<Checklist[] | null>(null)
  const [versionError, setVersionError] = useState('')
  const [versionAttempt, setVersionAttempt] = useState(0)
  const [versionId, setVersionId] = useState(receipt.current?.version ?? '')
  const [jobId, setJobId] = useState(() => {
    const q = new URLSearchParams(location.search)
    return q.get('group_id') === group && q.get('conversation_id') === conversation.conversation_id ? q.get('job_id') ?? '' : ''
  })
  const [job, setJob] = useState<Job | null>(null)
  const [jobError, setJobError] = useState('')
  const [pollAttempt, setPollAttempt] = useState(0)
  const [pending, setPending] = useState(false)
  const [sendError, setSendError] = useState('')
  const requestActive = useRef(false)
  const postController = useRef<AbortController | null>(null)
  const retryKeys = useRef(new Map<string, string>())
  const mounted = useRef(false)
  const notified = useRef('')
  const completed = useRef(onCompleted)
  useEffect(() => { completed.current = onCompleted }, [onCompleted])
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; postController.current?.abort() }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setStatus(null); setStatusError(''); setStatusMissing(false)
    analysisApi.get<ModelStatus>('/api/model-status', { signal: controller.signal }).then(({ data }) => {
      if (!controller.signal.aborted) setStatus(data)
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      setStatus(null)
      if (axios.isAxiosError(error) && error.response?.status === 404) setStatusMissing(true)
      else setStatusError(explain(error, 'Не удалось проверить готовность модели. Повторите запрос.'))
    })
    return () => controller.abort()
  }, [statusAttempt])

  useEffect(() => {
    const controller = new AbortController()
    setVersionError(''); setVersions(null)
    async function load() {
      try {
        const { data } = await analysisApi.get<ChecklistSummary[]>('/api/checklists', { signal: controller.signal })
        // Summary channels reflect a mutable draft; filter only the saved rule snapshots.
        const responses = await Promise.all(data.map(item => analysisApi.get<Checklist[]>(
          `/api/checklists/${enc(item.checklist_id)}/versions`, { signal: controller.signal },
        )))
        if (!controller.signal.aborted) setVersions(responses.flatMap(response => response.data)
          .filter(version => version.channels.includes(conversation.channel)))
      } catch (error) { if (!controller.signal.aborted) setVersionError(explain(error)) }
    }
    void load()
    return () => controller.abort()
  }, [conversation.channel, versionAttempt])

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    setJobError('')
    async function poll() {
      try {
        const { data } = await analysisApi.get<Job>('/api/evaluation-jobs/' + enc(jobId), { signal: controller.signal })
        if (controller.signal.aborted) return
        if (data.group_id !== group || !data.items.some(item => item.conversation_id === conversation.conversation_id)) {
          setJobError('Это задание относится к другой группе или диалогу. Выберите нужную карточку.')
          return
        }
        setJob(data)
        const item = data.items.find(row => row.conversation_id === conversation.conversation_id)
        if (terminal(data.status) && item?.evaluation_id && notified.current !== data.job_id) {
          notified.current = data.job_id
          completed.current(item.evaluation_id)
        }
        if (active(data.status)) timer = setTimeout(() => { void poll() }, 1200)
      } catch (error) { if (!controller.signal.aborted) setJobError(explain(error)) }
    }
    void poll()
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
  }, [jobId, group, conversation.conversation_id, pollAttempt])

  function openJob(id: string) {
    setJob(null); setJobError(''); setSendError(''); notified.current = ''; setJobId(id)
    const q = new URLSearchParams(location.search)
    q.set('group_id', group); q.set('conversation_id', conversation.conversation_id); q.set('job_id', id)
    history.replaceState(null, '', location.pathname + '?' + q.toString())
  }
  const selectedVersion = versions?.find(version => version.checklist_version_id === versionId)
    ?? versions?.find(version => version.checklist_version_id === (conversation.channel === 'voice call' ? 'freedom-call@1' : 'freedom-chat@1'))
    ?? versions?.[0]
  async function launch() {
    if (requestActive.current || !selectedVersion || !status?.ready) return
    const version = selectedVersion.checklist_version_id
    const key = receipt.current?.version === version ? receipt.current.key : freshKey()
    receipt.current = { key, version }; saveReceipt(savedKey, receipt.current)
    await send('/api/evaluation-jobs', key, {
      group_id: group, conversation_ids: [conversation.conversation_id], checklist_version_id: version,
    }, true)
  }
  async function retry() {
    if (!job || requestActive.current) return
    if (job.retry_job_id) { openJob(job.retry_job_id); return }
    if (!status?.ready) return
    let key = retryKeys.current.get(job.job_id)
    if (!key) { key = freshKey(); retryKeys.current.set(job.job_id, key) }
    await send('/api/evaluation-jobs/' + enc(job.job_id) + '/retry', key)
  }
  async function send(url: string, key: string, body?: unknown, isNew = false) {
    requestActive.current = true; setPending(true); setSendError('')
    const controller = new AbortController(); postController.current = controller
    try {
      const { data } = await analysisApi.post<{ job_id: string }>(url, body, {
        signal: controller.signal, headers: { 'Idempotency-Key': key },
      })
      if (!mounted.current) return
      if (isNew) { receipt.current = null; saveReceipt(savedKey, null) }
      openJob(data.job_id)
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) {
        if (!isNew && axios.isAxiosError(error) && error.response?.status === 409) {
          setSendError(explain(error) + ' Обновляем задание. Если повтор уже создан, откройте его после обновления.')
          // Another client may have created the retry. Refresh its link without another POST.
          setPollAttempt(attempt => attempt + 1)
        } else setSendError(explain(error))
      }
    }
    finally { requestActive.current = false; if (mounted.current) setPending(false) }
  }
  const busy = pending || Boolean(jobId && !job) || Boolean(job && active(job.status))
  const failed = job?.items.find(item => item.conversation_id === conversation.conversation_id)
  const retryable = job && ['partial', 'failed', 'interrupted'].includes(job.status)
  const finished = job ? ['completed', 'partial', 'failed', 'interrupted'].reduce((sum, key) => sum + (job.counts[key] ?? 0), 0) : 0

  return <section className="an-launcher an-panel" aria-label="Оценка моделью">
    <div className="an-launch-heading"><div><span className="an-eyebrow">Модуль оценки №2</span><h2>Новая оценка диалога</h2></div>
      <button type="button" className="an-text-button" disabled={pending} onClick={() => setStatusAttempt(n => n + 1)}><RefreshCw size={13} />Обновить статус модели</button></div>
    {!status && !statusError && !statusMissing && <p className="an-hint" role="status">Проверяем готовность модели…</p>}
    {statusMissing && <div className="an-note">Сервер запущен без маршрутов модели. Запустите общий сервер через factory участника №2: <code>backend.app.model_app:create_configured_app --factory</code>. Порт остаётся 8765; второй сервер не нужен.</div>}
    {statusError && <p className="an-error" role="alert">{statusError}</p>}
    {status && <p className={status.ready ? 'an-model-ready' : 'an-note'} role="status">{status.ready
      ? `Модель готова: ${status.provider ?? 'поставщик'} / ${status.model ?? 'модель'}. Версия инструкции: ${status.prompt_version}.`
      : modelErrorText(status.reason ?? 'model_not_ready')}</p>}
    {status?.ready && <p className="an-hint">Обрабатывается только выбранный диалог. Новая оценка сохраняется отдельно; прежние ответы и апелляции остаются доступны. Аудио моделью не анализируется.</p>}
    <div className="an-launch-controls"><label>Версия правил для новой оценки<select aria-label="Версия правил для новой оценки" value={selectedVersion?.checklist_version_id ?? ''} onChange={event => setVersionId(event.target.value)} disabled={!versions?.length || Boolean(busy)}>
      {!versions?.length && <option value="">{versions ? 'Нет совместимых сохранённых версий' : 'Загружаем версии…'}</option>}
      {versions?.map(version => <option key={version.checklist_version_id} value={version.checklist_version_id}>{version.title} · {version.checklist_version_id}</option>)}
    </select></label><button type="button" className="an-button an-secondary" onClick={() => setVersionAttempt(n => n + 1)} disabled={Boolean(busy)}>Обновить версии</button>
      <button type="button" className="an-button an-primary" disabled={!status?.ready || !selectedVersion || Boolean(busy)} onClick={() => void launch()}><Play size={15} />{pending ? 'Отправляем…' : 'Оценить диалог'}</button></div>
    {versionError && <p className="an-error" role="alert">{versionError}</p>}
    {sendError && <p className="an-error" role="alert">{sendError} Повторная отправка с той же версией использует прежний ключ запроса.</p>}
    {jobId && <div className="an-job-progress" role="region" aria-label="Задание оценки"><div className="an-job-heading"><strong>{job ? modelRunStatus(job.status) : 'Загружаем задание…'}</strong><code>{jobId}</code></div>
      {jobError ? <p className="an-error" role="alert">{jobError} <button type="button" className="an-text-button" onClick={() => setPollAttempt(n => n + 1)}>Повторить обновление задания</button></p> : job && <>
        <p role="status">Обработано {finished} из {job.total}. В очереди: {job.counts.queued ?? 0}, выполняется: {job.counts.running ?? 0}, ошибок: {job.counts.failed ?? 0}.</p>
        <progress value={finished} max={Math.max(1, job.total)} aria-label="Ход оценки" />
        <p className="an-hint">Версия задания: {job.checklist_version_id}. {job.status === 'interrupted' ? 'Сервер прервал запуск; автоматического повтора нет.' : job.status === 'partial' ? 'Есть частичные или ошибочные ответы; итог не подставляется.' : ''}</p>
        {failed?.error_code && <p className="an-error">{modelErrorText(failed.error_code)} <code>{failed.error_code}</code></p>}
        {retryable && <button type="button" className="an-button an-secondary" disabled={pending || (!job.retry_job_id && !status?.ready)} onClick={() => void retry()}><RefreshCw size={14} />{job.retry_job_id ? 'Открыть задание повтора' : 'Повторить неуспешные'}</button>}
      </>}
    </div>}
    <ModelRunHistory group={group} conversationId={conversation.conversation_id} refreshKey={jobId + '/' + (job?.status ?? '')} />
  </section>
}
