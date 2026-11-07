import { useEffect, useState } from 'react'
import axios from 'axios'
import { Link2, RefreshCw } from 'lucide-react'
import { people } from '../dialogues/data'
import { analysisApi, enc, explain } from './api'
import { modelErrorText, modelRunStatus } from './modelErrors'
import type { SnapshotEvidence } from './modelTypes'
import type { Card, Evidence } from './types'

function dateText(value: string | null | undefined) {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('ru-RU') : 'Не указано'
}

function elapsedText(value: number | null | undefined) {
  return value != null && Number.isFinite(value) && value >= 0
    ? (value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 3 }) + ' сек.'
    : 'Не измерено'
}

function costText(value: number | null | undefined) {
  return value != null && Number.isFinite(value) && value >= 0
    ? value.toLocaleString('ru-RU', { maximumFractionDigits: 8 }) + ' USD'
    : 'Неизвестна — нет полных данных о токенах или тарифах'
}

type QuotesResponse = { evaluationId: string; data?: SnapshotEvidence[]; error?: string }

export default function ModelAuditPanel({ card, onJump }: { card: Card; onJump: (ref: Evidence) => void }) {
  const run = card.runtime.model_run
  const belongsToCard = run?.evaluation_id === card.evaluation.evaluation_id
    && run.group_id === card.evaluation.group_id
    && run.conversation_id === card.evaluation.conversation_id
    && run.checklist_version_id === card.evaluation.checklist_version_id
  const evaluationId = card.evaluation.evaluation_id
  const [quotes, setQuotes] = useState<QuotesResponse | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!belongsToCard) return
    const controller = new AbortController()
    analysisApi.get<SnapshotEvidence[]>(`/api/evaluation-runs/${enc(evaluationId)}/evidence`, {
      signal: controller.signal,
    }).then(({ data }) => {
      if (controller.signal.aborted) return
      if (!Array.isArray(data) || data.some(quote =>
        quote.reference.group_id !== card.evaluation.group_id
        || quote.reference.conversation_id !== card.evaluation.conversation_id
        || !Array.isArray(quote.messages),
      )) {
        setQuotes({ evaluationId, error: 'Доказательства не соответствуют выбранной карточке. Обновите данные.' })
        return
      }
      setQuotes({ evaluationId, data })
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      const status = axios.isAxiosError(error) ? error.response?.status : null
      const message = status === 404
        ? 'Сохранённые цитаты этого запуска недоступны. Проверьте запуск сервера с подключённым модулем модели; расшифровка остаётся доступной.'
        : status === 409 ? 'В запуске пока нет проверенного результата с цитатами.'
          : explain(error, 'Не удалось загрузить исходные цитаты. Расшифровка карточки остаётся доступной.')
      setQuotes({ evaluationId, error: message })
    })
    return () => controller.abort()
  }, [belongsToCard, evaluationId, card.evaluation.group_id, card.evaluation.conversation_id, attempt])

  if (!run) return <div className="an-model-audit">
    <dl>
      <div><dt>Модель</dt><dd>{card.evaluation.generation.model_id ?? 'Не указана'}</dd></div>
      <div><dt>Завершено</dt><dd>{dateText(card.runtime.finished_at)}</dd></div>
      <div><dt>Стоимость запуска</dt><dd>Неизвестна — метаданные модели не сохранены</dd></div>
    </dl>
    <p className="an-hint">Для этой сохранённой карточки аудит запуска недоступен. Неизвестная стоимость не равна нулевому расходу.</p>
  </div>
  if (!belongsToCard) return <p className="an-error" role="alert">Метаданные относятся к другому запуску. Обновите карточку; чужой аудит не отображается.</p>

  const response = quotes?.evaluationId === evaluationId ? quotes : null
  const visibleQuotes = expanded ? response?.data : response?.data?.slice(0, 8)
  const demo = run.generation_mode === 'demo'
  return <div className="an-model-audit">
    <dl>
      <div><dt>Состояние запуска</dt><dd>{modelRunStatus(run.status)}</dd></div>
      <div><dt>Сервис</dt><dd>{run.provider}</dd></div>
      <div><dt>Модель в ответе</dt><dd>{run.resolved_model ?? 'Не указана сервисом'}</dd></div>
      <div><dt>Настроенная модель</dt><dd>{run.configured_model}</dd></div>
      <div><dt>Версия инструкции модели</dt><dd>{run.prompt_version}</dd></div>
      <div><dt>Версия справочника</dt><dd>{run.knowledge_version ?? 'Справочник не передавался'}</dd></div>
      <div><dt>Начало оценки</dt><dd>{dateText(run.started_at)}</dd></div>
      <div><dt>Завершено</dt><dd>{dateText(run.finished_at)}</dd></div>
      <div><dt>Время оценки модели</dt><dd>{elapsedText(run.elapsed_ms)}</dd></div>
      <div><dt>Попыток</dt><dd>{run.attempts.length}</dd></div>
      <div><dt>Входные токены</dt><dd>{run.token_usage?.input_tokens.toLocaleString('ru-RU') ?? 'Неизвестно'}</dd></div>
      <div><dt>Выходные токены</dt><dd>{run.token_usage?.output_tokens.toLocaleString('ru-RU') ?? 'Неизвестно'}</dd></div>
      <div><dt>{demo ? 'Учебная оценка стоимости' : 'Оценка стоимости запуска'}</dt><dd>{costText(run.estimated_cost_usd)}</dd></div>
      <div><dt>Аудио анализировалось</dt><dd>{run.audio_analysed ? 'Да' : 'Нет — оценка по тексту и меткам времени'}</dd></div>
    </dl>
    <p className="an-hint">{demo
      ? 'Учебный запуск: токены и расчёт по тарифам не подтверждают реальный расход средств.'
      : 'Стоимость — оценка по серверным тарифам за все известные попытки; скидки и поправки сервиса могут отличаться.'}</p>
    {run.error_code && <p className="an-note">{modelErrorText(run.error_code)}</p>}
    <details className="an-model-attempts"><summary>Попытки и причины повторов</summary>
      {run.attempts.length === 0 ? <p className="an-hint">Вызов сервиса ещё не был выполнен.</p> : <ol>
        {run.attempts.map(item => <li key={item.number}>
          <strong>Попытка {item.number} · {modelRunStatus(item.status)}</strong>
          <p>{elapsedText(item.elapsed_ms)} · {item.provider && item.provider + ' / '}{item.resolved_model ?? item.configured_model ?? 'Модель не сообщена'}</p>
          <p>Токены: вход {item.usage?.input_tokens.toLocaleString('ru-RU') ?? 'неизвестен'}, выход {item.usage?.output_tokens.toLocaleString('ru-RU') ?? 'неизвестен'}.</p>
          {item.error_code && <p className="an-model-attempt-error">{modelErrorText(item.error_code)}</p>}
        </li>)}
      </ol>}
    </details>
    <details className="an-model-quotes"><summary>Исходные доказательства AI из сохранённого входа</summary>
      <p className="an-hint">Цитаты извлекает сервер из исходного запуска. Решения проверяющего не переписывают эти сообщения.</p>
      {response?.error ? <p className="an-error" role="alert">{response.error} <button type="button" className="an-text-button" onClick={() => { setQuotes(null); setAttempt(n => n + 1) }}><RefreshCw size={13} />Повторить загрузку цитат</button></p>
        : !response?.data ? <p role="status" className="an-hint">Загружаем исходные цитаты…</p>
          : response.data.length === 0 ? <p className="an-hint">Для этого запуска сохранённых цитат нет.</p>
            : <>
              <ol>{visibleQuotes?.map((quote, index) => <li key={index}>
                <strong>{card.checklist.criteria.find(c => c.id === quote.criterion_id)?.title ?? quote.criterion_id}</strong>
                {quote.messages.slice(0, 3).map(message => <blockquote key={message.message_id}>
                  <span>{people[message.speaker]} · {message.timestamp}</span><p>{message.text}</p><code>{message.message_id}</code>
                </blockquote>)}
                {quote.messages.length > 3 && <p className="an-hint">Ещё {quote.messages.length - 3} реплик доступны при переходе к полному фрагменту.</p>}
                <button className="an-text-button" type="button" onClick={() => onJump(quote.reference)}><Link2 size={13} />Открыть исходный фрагмент</button>
              </li>)}</ol>
              {!expanded && response.data.length > 8 && <button className="an-text-button" type="button" onClick={() => setExpanded(true)}>Показать остальные доказательства ({response.data.length - 8})</button>}
            </>}
    </details>
    <details className="an-model-identifiers"><summary>Идентификаторы запуска</summary>
      <code>{run.evaluation_id}</code><code>{run.checklist_version_id}</code>
      {run.input_fingerprint && <code>{run.input_fingerprint}</code>}
    </details>
  </div>
}
