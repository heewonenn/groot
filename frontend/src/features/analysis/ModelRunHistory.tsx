import { useEffect, useState } from 'react'
import axios from 'axios'
import { RefreshCw } from 'lucide-react'
import { analysisApi, enc, explain } from './api'
import { modelErrorText, modelRunStatus } from './modelErrors'
import type { ModelRun } from './modelTypes'

type History = { key: string; data?: ModelRun[]; error?: string; unavailable?: boolean }

export default function ModelRunHistory({ group, conversationId, refreshKey = '' }: {
  group: string; conversationId: string; refreshKey?: string
}) {
  const key = JSON.stringify([group, conversationId, refreshKey])
  const [response, setResponse] = useState<History | null>(null)
  const [retry, setRetry] = useState(0)
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (!group || !conversationId) return
    const controller = new AbortController()
    analysisApi.get<ModelRun[]>(`/api/evaluation-runs?group_id=${enc(group)}&conversation_id=${enc(conversationId)}`, {
      signal: controller.signal,
    }).then(({ data }) => {
      if (controller.signal.aborted) return
      if (!Array.isArray(data) || data.some(run => run.group_id !== group || run.conversation_id !== conversationId)) {
        setResponse({ key, error: 'История не соответствует выбранному диалогу. Обновите список.' })
        return
      }
      setResponse({ key, data })
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        setResponse({ key, unavailable: true })
      } else setResponse({ key, error: explain(error, 'Не удалось получить историю модели. Сохранённые карточки остаются доступны.') })
    })
    return () => controller.abort()
  }, [group, conversationId, key, retry])
  const current = response?.key === key ? response : null
  if (!conversationId || current?.unavailable) return null
  const visible = expanded ? current?.data : current?.data?.slice(0, 5)
  const reload = () => { setResponse(null); setRetry(n => n + 1) }
  return <details className="an-model-history">
    <summary>История запусков этого диалога{current?.data ? ` (${current.data.length})` : ''}</summary>
    <p className="an-hint">Здесь показаны последние отдельные запуски, включая неудачные. Они могут относиться к разным заданиям и версиям чек-листа; сверяйте ID и время.</p>
    <button type="button" className="an-text-button" onClick={reload}><RefreshCw size={13} />Обновить историю запусков</button>
    {current?.error ? <p className="an-error" role="alert">{current.error} <button type="button" className="an-text-button" onClick={reload}>Повторить загрузку истории</button></p>
      : !current?.data ? <p className="an-hint" role="status">Загружаем историю модели…</p>
        : current.data.length === 0 ? <p className="an-hint">В этом диалоге ещё нет сохранённых запусков модели.</p>
          : <>
            <ol>{visible?.map(run => <li key={run.evaluation_id}>
              <div className="an-model-history-heading"><strong>{modelRunStatus(run.status)}</strong><span>{run.generation_mode === 'demo' ? 'Демонстрационный запуск' : run.generation_mode === 'cache' ? 'Кеш' : 'Запуск модели'}</span></div>
              <p>{run.resolved_model ?? run.configured_model} · {run.checklist_version_id}</p>
              <p>{Number.isFinite(Date.parse(run.started_at)) ? new Date(run.started_at).toLocaleString('ru-RU') : 'Время не указано'} · попыток: {run.attempts.length} · {run.elapsed_ms != null && Number.isFinite(run.elapsed_ms) ? (run.elapsed_ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 3 }) + ' сек.' : 'длительность неизвестна'}</p>
              {run.error_code && <p className="an-model-attempt-error">{modelErrorText(run.error_code)}</p>}
              <p>{run.generation_mode === 'demo' ? 'Учебная оценка стоимости' : 'Оценка стоимости'}: {run.estimated_cost_usd != null && Number.isFinite(run.estimated_cost_usd) ? run.estimated_cost_usd.toLocaleString('ru-RU', { maximumFractionDigits: 8 }) + ' USD' : 'неизвестна'}.</p>
              <code>{run.evaluation_id}</code>
            </li>)}</ol>
            {!expanded && current.data.length > 5 && <button type="button" className="an-text-button" onClick={() => setExpanded(true)}>Показать остальные запуски ({current.data.length - 5})</button>}
            {current.data.length >= 100 && <p className="an-hint">API показывает последние 100 запусков этого диалога.</p>}
          </>}
  </details>
}
