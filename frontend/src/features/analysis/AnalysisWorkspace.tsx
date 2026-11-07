import { useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowLeft, ArrowRight, Check, ChevronDown, CircleAlert, ClipboardCheck, FileCheck2, Flag, Headphones, History, LayoutDashboard, Link2, ListChecks, MessageSquare, MessageSquarePlus, Minus, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react'
import CallRecording from '../dialogues/CallRecording'
import { people, useData } from '../dialogues/data'
import type { Conversation, Message, Summary } from '../dialogues/data'
import { analysisApi, downloadJson, enc, explain, route, valueText } from './api'
import ExportDialog from './ExportDialog'
import ReviewDialog from './ReviewDialog'
import type { Answer, Appeal, Card, Criterion, EvaluationSummary, Evidence } from './types'
import './analysis.css'

const statusLabel = (s: string) => s === 'completed' ? 'Оценка готова' : s === 'partial' ? 'Нужна проверка' : s === 'failed' ? 'Ошибка оценки' : s
const scoreText = (value: string | null | undefined) => value === null || value === undefined ? '—' : Number(value).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dateText = (value: string | null) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString('ru-RU') : '—'
const durationText = (start: string | null, end: string | null) => start && end && Date.parse(end) >= Date.parse(start)
  ? ((Date.parse(end) - Date.parse(start)) / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' сек.' : 'Не измерено'

function Notice({ error, children, onRetry }: { error?: boolean; children: React.ReactNode; onRetry?: () => void }) {
  return <div className={error ? 'an-error' : 'an-note'} role={error ? 'alert' : 'status'}>{children}{onRetry && <button type="button" className="an-text-button" onClick={onRetry}><RefreshCw size={14} />Повторить</button>}</div>
}

function Sidebar({ view, group }: { view: 'analysis' | 'appeals'; group: string }) {
  return <aside className="an-sidebar"><a className="an-brand" href={route('analysis', group)} aria-label="CQA — рабочее место"><span className="an-brand-symbol"><ShieldCheck size={23} /></span><span>CQA<small>QUALITY WORKSPACE</small></span></a>
    <div className="an-sidebar-caption">Рабочее место</div><nav aria-label="Главное меню">
      <a href={route('analysis', group)} aria-current={view === 'analysis' ? 'page' : undefined}><MessageSquare size={18} /> Анализ диалога</a>
      <a href={route('conversations', group)}><Headphones size={18} /> Диалоги</a>
      <a href={route('checklists')}><ListChecks size={18} /> Чек-листы</a>
      <a href={route('appeals', group)} aria-current={view === 'appeals' ? 'page' : undefined}><ClipboardCheck size={18} /> Апелляции</a>
    </nav><div className="an-sidebar-bottom"><div className="an-team-avatar">G</div><span>WE ARE GROOT<small>Командный проект · MVP</small></span></div>
  </aside>
}

export default function AnalysisWorkspace({ view = 'analysis' }: { view?: 'analysis' | 'appeals' }) {
  const query = new URLSearchParams(window.location.search)
  const [group, setGroup] = useState(query.get('group_id') || 'SYNTHETIC')
  const [groupInput, setGroupInput] = useState(group)
  const [conversationId, setConversationId] = useState(query.get('conversation_id') || '')
  const [evaluationId, setEvaluationId] = useState(query.get('evaluation_id') || '')
  const [exportOpen, setExportOpen] = useState(false)
  const summaries = useData<Summary[]>(view === 'analysis' ? `/api/groups/${enc(group)}/conversations` : null)
  const selectedConversation = conversationId || summaries.data?.[0]?.conversation_id || ''
  const evaluations = useData<EvaluationSummary[]>(view === 'analysis' && selectedConversation ? `/api/evaluations?group_id=${enc(group)}&conversation_id=${enc(selectedConversation)}` : null)
  const selectedEvaluation = evaluations.data?.some(e => e.evaluation_id === evaluationId) ? evaluationId : evaluations.data?.[0]?.evaluation_id || ''
  const capabilities = useData<{ model_available: boolean }>('/api/capabilities')
  useEffect(() => {
    if (view !== 'analysis') return
    const q = new URLSearchParams(window.location.search)
    if (q.get('group_id') !== group || (q.get('conversation_id') && q.get('conversation_id') !== selectedConversation)) {
      q.delete('message_id'); q.delete('span_start'); q.delete('span_end')
    }
    q.set('group_id', group)
    if (selectedConversation) q.set('conversation_id', selectedConversation)
    else q.delete('conversation_id')
    if (selectedEvaluation) q.set('evaluation_id', selectedEvaluation)
    else q.delete('evaluation_id')
    window.history.replaceState(null, '', window.location.pathname + '?' + q.toString())
  }, [view, group, selectedConversation, selectedEvaluation])

  function applyGroup(e: React.SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    if (groupInput.trim()) { setGroup(groupInput.trim()); setConversationId(''); setEvaluationId('') }
  }
  return <div className="an-workspace"><a href="#an-main" className="an-skip">К содержимому</a><Sidebar view={view} group={group} />
    <div className="an-main-wrap"><header className="an-topbar"><span><LayoutDashboard size={15} /> Рабочее место контроля качества</span><span className="an-topbar-tag"><span /> CQA · MVP</span></header>
      <main id="an-main" className="an-main"><div className="an-page-heading"><div><div className="an-eyebrow">Контроль качества / {view === 'appeals' ? 'Пересмотр' : 'Диалоги'}</div><h1>{view === 'appeals' ? 'Апелляции' : 'Анализ разговора'}<span className="an-heading-dot">.</span></h1><p>{view === 'appeals' ? 'Обращения, решения и сохранённая история проверки.' : 'От реплики к решению — всё необходимое на одном экране.'}</p></div><button type="button" className="an-button an-secondary" onClick={() => setExportOpen(true)}><ArrowDownToLine size={16} /> Экспорт для сдачи</button></div>
        <div className="an-selection"><form onSubmit={applyGroup} className="an-group-form"><label>Группа<input value={groupInput} onChange={e => setGroupInput(e.target.value)} required maxLength={200} /></label><button type="submit" className="an-icon-button" aria-label="Открыть группу"><ArrowRight size={18} /></button></form>
          {view === 'analysis' && <><label>Диалог<select aria-label="Диалог" value={selectedConversation} onChange={e => { setConversationId(e.target.value); setEvaluationId('') }} disabled={!summaries.data?.length}><option value="" disabled>Выберите диалог</option>{summaries.data?.map(c => <option key={c.conversation_id} value={c.conversation_id}>{c.conversation_id} · {c.language} · {c.channel === 'voice call' ? 'звонок' : 'чат'}</option>)}</select></label><label>Оценка<select aria-label="Оценка" value={selectedEvaluation} onChange={e => setEvaluationId(e.target.value)} disabled={!evaluations.data?.length}><option value="" disabled>Нет сохранённых оценок</option>{evaluations.data?.map(e => <option key={e.evaluation_id} value={e.evaluation_id}>{e.checklist_version_id} · {e.generation.mode === 'demo' ? 'демо' : e.generation.mode} · {e.evaluation_id}</option>)}</select></label></>}
        </div>
        {capabilities.data?.model_available === false && <div className="an-integration-note"><Sparkles size={16} /><span>Модуль модели №2 ещё не подключён. Здесь доступны сохранённые оценки и их пересмотр; новые ответы AI не имитируются.</span></div>}
        {view === 'appeals' ? <AppealsOverview group={group} /> : summaries.error ? <Notice error onRetry={summaries.retry}>{summaries.error}</Notice> : !summaries.data ? <Notice>Загружаем диалоги…</Notice> : summaries.data.length === 0 ? <Notice>В группе пока нет диалогов. Импортируйте набор на странице «Диалоги».</Notice> : evaluations.error ? <Notice error onRetry={evaluations.retry}>{evaluations.error}</Notice> : !evaluations.data ? <Notice>Ищем сохранённые оценки…</Notice> : <ConversationArea key={group + '/' + selectedConversation + '/' + selectedEvaluation} group={group} conversationId={selectedConversation} evaluationId={selectedEvaluation} />}
      </main><footer className="an-footer"><span>CQA · WE ARE GROOT</span><span>Доказательства по исходным message_id · Версии правил сохраняются</span></footer></div>
    {exportOpen && <ExportDialog group={group} onClose={() => setExportOpen(false)} />}
  </div>
}

function ConversationArea({ group, conversationId, evaluationId }: { group: string; conversationId: string; evaluationId: string }) {
  const path = `/api/groups/${enc(group)}/conversations/${enc(conversationId)}`
  const conversation = useData<Conversation>(path)
  const resource = useData<Card>(evaluationId ? `/api/evaluation-cards/${enc(evaluationId)}` : null)
  const [saved, setSaved] = useState<Card | null>(null)
  const card = saved ?? resource.data
  if (conversation.error) return <Notice error onRetry={conversation.retry}>{conversation.error}</Notice>
  if (!conversation.data) return <Notice>Загружаем расшифровку…</Notice>
  if (resource.error) return <Notice error onRetry={resource.retry}>{resource.error}</Notice>
  if (evaluationId && !card) return <Notice>Загружаем оценку и исходную версию правил…</Notice>
  return <AnalysisContent conversation={conversation.data} card={card} path={path} onSaved={setSaved} onRefresh={() => { setSaved(null); resource.retry() }} />
}

function AnalysisContent({ conversation, card, path, onSaved, onRefresh }: { conversation: Conversation; card?: Card; path: string; onSaved: (card: Card) => void; onRefresh: () => void }) {
  const [criterionId, setCriterionId] = useState('')
  const [selectedIds, setSelectedIds] = useState<string[]>(() => {
    const q = new URLSearchParams(window.location.search)
    const id = q.get('message_id')
    const start = conversation.transcript.findIndex(m => m.message_id === q.get('span_start'))
    const end = conversation.transcript.findIndex(m => m.message_id === q.get('span_end'))
    const rangeIds = start >= 0 && end >= start ? conversation.transcript.slice(start, end + 1).map(m => m.message_id) : []
    return conversation.transcript.filter(m => m.message_id === id || rangeIds.includes(m.message_id)).map(m => m.message_id)
  })
  const [review, setReview] = useState<{ criterion: Criterion; answer: Answer; appeal?: Appeal } | null>(null)
  const [proofError, setProofError] = useState('')
  const [proofPending, setProofPending] = useState(false)
  const [auditError, setAuditError] = useState('')
  const [auditPending, setAuditPending] = useState(false)
  const nodes = useRef(new Map<string, HTMLElement>())
  const criterionHeading = useRef<HTMLHeadingElement>(null)
  const selectedCriterion = card?.checklist.criteria.find(c => c.id === criterionId) ?? card?.checklist.criteria[0]
  const answer = card?.final_answers.find(a => a.criterion_id === selectedCriterion?.id)
  const original = card?.evaluation.model_result?.criteria.find(a => a.criterion_id === selectedCriterion?.id)
  const criterionCalculation = card?.evaluation.final_result?.criteria.find(a => a.criterion_id === selectedCriterion?.id)
  const selectedSet = new Set(selectedIds)
  const total = card?.evaluation.final_result?.total
  const percentage = total === undefined || total === null ? 0 : Math.min(100, Math.max(0, Number(total)))
  useEffect(() => {
    const node = selectedIds[0] ? nodes.current.get(selectedIds[0]) : null
    node?.focus({ preventScroll: true })
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [selectedIds])
  async function jump(ref: Evidence) {
    setProofError('')
    if (ref.group_id !== conversation.group_id || ref.conversation_id !== conversation.conversation_id) { setProofError('Доказательство относится к другой группе или диалогу.'); return }
    setProofPending(true)
    try {
      const { data } = await analysisApi.get<Message>(path + '/messages/' + enc(ref.message_id))
      if (data.message_id !== ref.message_id) throw new Error('mismatch')
      const start = conversation.transcript.findIndex(m => m.message_id === (ref.span?.start_message_id ?? ref.message_id))
      const end = ref.span ? conversation.transcript.findIndex(m => m.message_id === ref.span?.end_message_id) : start
      if (start < 0 || end < start) throw new Error('range')
      const rangeIds = conversation.transcript.slice(start, end + 1).map(m => m.message_id)
      setSelectedIds(conversation.transcript.filter(m => m.message_id === ref.message_id || rangeIds.includes(m.message_id)).map(m => m.message_id))
      const q = new URLSearchParams(window.location.search)
      q.set('group_id', conversation.group_id); q.set('conversation_id', conversation.conversation_id); q.set('message_id', ref.message_id)
      if (ref.span) { q.set('span_start', ref.span.start_message_id); q.set('span_end', ref.span.end_message_id) }
      else { q.delete('span_start'); q.delete('span_end') }
      if (card) q.set('evaluation_id', card.evaluation.evaluation_id)
      window.history.replaceState(null, '', window.location.pathname + '?' + q.toString())
    } catch (err) { setProofError(explain(err, 'Не удалось открыть исходную реплику доказательства. Обновите карточку и повторите.')) }
    finally { setProofPending(false) }
  }
  async function audit() {
    if (!card) return
    setAuditPending(true); setAuditError('')
    try { const { data } = await analysisApi.get(`/api/evaluation-cards/${enc(card.evaluation.evaluation_id)}/audit`); downloadJson(data, 'cqa-audit-' + card.evaluation.evaluation_id + '.json') }
    catch (err) { setAuditError(explain(err)) } finally { setAuditPending(false) }
  }
  const isDemo = card?.evaluation.generation.mode === 'demo'
  const openAppeal = card?.appeals.find(a => a.criterion_id === selectedCriterion?.id && a.status === 'open')

  return <>
    <div className="an-context-line"><span className="an-channel"><MessageSquare size={14} />{conversation.channel === 'voice call' ? 'Звонок' : 'Чат'}</span><strong>{conversation.conversation_id}</strong><span>{conversation.language}</span><span>{conversation.message_count} реплик</span>{card && <span className={'an-status an-status-' + card.final_status}>{statusLabel(card.final_status)}</span>}</div>
    {isDemo && <div className="an-demo-banner"><span className="an-demo-icon"><Sparkles size={18} /></span><div><strong>Демонстрационный пример</strong><p>{card.evaluation.generation.note || 'Искусственный диалог и вручную заданные ответы для проверки интерфейса.'} Это не результат модели и не Ground Truth.</p></div></div>}
    {conversation.channel === 'voice call' ? conversation.has_audio ? <CallRecording path={path} messages={conversation.transcript} evidenceId={selectedIds[0]} onSelect={id => setSelectedIds([id])} /> : <div className="an-audio-empty"><Headphones size={22} /><div><strong>Запись звонка недоступна</strong><p>Расшифровка сохранена. Для прослушивания нужна запись от участника №4; позиции в аудио не подставляются из времени реплик.</p></div></div> : <div className="an-chat-strip"><span className="an-chat-strip-icon"><MessageSquare size={21} /></span><div><strong>Текстовый диалог</strong><p>Исходная последовательность сообщений · все роли сохранены</p></div><span className="an-strip-badge"><ShieldCheck size={14} /> Проверяемые доказательства</span></div>}
    <div className="an-analysis-grid"><section className="an-panel an-transcript" aria-labelledby="an-transcript-heading"><div className="an-panel-heading"><div><span className="an-eyebrow">Исходный диалог</span><h2 id="an-transcript-heading">Расшифровка</h2></div><span className="an-count">{conversation.message_count}</span></div>
      <div className="an-transcript-toolbar"><p className="an-transcript-hint">Выделенный фрагмент связан с выбранным критерием.</p>{card && selectedIds.length > 0 && <button className="an-text-button" type="button" onClick={() => { criterionHeading.current?.focus({ preventScroll: true }); criterionHeading.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}><ArrowRight size={13} />Вернуться к критерию</button>}</div>
      <ol className="an-messages">{conversation.transcript.map((m, i) => <li key={m.message_id} ref={node => { if (node) nodes.current.set(m.message_id, node); else nodes.current.delete(m.message_id) }} className={'an-message an-speaker-' + m.speaker + (selectedSet.has(m.message_id) ? ' an-message-selected' : '')} tabIndex={-1} aria-current={selectedSet.has(m.message_id) ? 'true' : undefined} data-message-id={m.message_id} aria-label={'Реплика ' + (i + 1) + ', ' + people[m.speaker]}>
        <div className="an-message-heading"><span className="an-speaker-dot" /><strong>{people[m.speaker]}</strong><time>{m.timestamp}</time><button type="button" className="an-message-link" aria-label={'Выделить реплику ' + m.message_id} onClick={() => void jump({ group_id: conversation.group_id, conversation_id: conversation.conversation_id, message_id: m.message_id, kind: 'context', span: null })}><Link2 size={13} /></button></div><p>{m.text}</p><code>{m.message_id}</code>
      </li>)}</ol><div className="an-transcript-footer"><ShieldCheck size={13} /><span>ID реплик получены от сервера. Одинаковое время не объединяет сообщения.</span></div>
    </section>
    <section className="an-panel an-assessment" aria-labelledby="an-assessment-heading"><div className="an-panel-heading"><div><span className="an-eyebrow">Проверка по чек-листу</span><h2 id="an-assessment-heading">Оценка качества</h2></div>{card && <button type="button" className="an-icon-button" onClick={onRefresh} aria-label="Обновить карточку"><RefreshCw size={16} /></button>}</div>
      {!card ? <div className="an-empty-evaluation"><FileCheck2 size={38} /><h3>Оценка ещё не получена</h3><p>Диалог доступен для просмотра. После запуска модели участником №2 здесь появятся критерии, обоснования и доказательства.</p><a href={route('conversations', conversation.group_id, conversation.conversation_id)} className="an-button an-secondary">Открыть диалог <ArrowRight size={15} /></a></div> : <>
        <div className="an-score-summary"><div className={'an-score-circle' + (total == null ? ' an-score-unknown' : '')} style={{ '--an-progress': percentage + '%' } as React.CSSProperties} aria-label="Итоговая оценка"><div><strong>{total == null ? '—' : Number(total).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}</strong><span>{total == null ? 'нет итога' : 'из 100'}</span></div></div><div className="an-score-meta"><span className="an-score-label">{card.revision ? 'После пересмотра' : 'Исходная оценка AI'}</span><p>{card.final_status === 'partial' ? 'Часть ответов требует решения проверяющего.' : total == null ? 'Нет применимых критериев для числового итога.' : 'Итог рассчитан общим модулем №1.'}</p><div><span>Исходная оценка AI</span><strong>{scoreText(card.evaluation.original_result?.total)}</strong></div>{card.revision > 0 && <div><span>После пересмотра</span><strong>{scoreText(total)}</strong></div>}</div></div>
        <div className={'an-gross-flag' + (card.evaluation.final_result?.gross_error ? ' an-gross-flag-active' : '')}><Flag size={15} /><span>{card.evaluation.final_result ? card.evaluation.final_result.gross_error ? 'Грубая ошибка зафиксирована' : 'Грубая ошибка не зафиксирована' : 'Флаг грубой ошибки: итог ещё не определён'}</span><small>Не меняет балл</small></div>
        <div className="an-criteria-list" aria-label="Критерии оценки">{card.checklist.criteria.map(c => {
          const a = card.final_answers.find(v => v.criterion_id === c.id)
          const calculation = card.evaluation.final_result?.criteria.find(v => v.criterion_id === c.id)
          const unresolved = !a || a.decision_status === 'unresolved'
          const na = a?.applicability === 'not_applicable'
          const negative = !unresolved && !na && (c.answer_type === 'flag' ? a?.value === true : a?.value === false || (typeof a?.value === 'number' && a.value === c.levels[0]?.value))
          const selected = selectedCriterion?.id === c.id
          return <button key={c.id} type="button" className={'an-criterion' + (selected ? ' an-criterion-selected' : '')} aria-label={'Открыть критерий ' + c.title} aria-pressed={selected} onClick={() => setCriterionId(c.id)}>
            <span className={'an-criterion-status' + (unresolved ? ' an-pending' : na ? ' an-na' : negative ? ' an-negative' : '')}>{unresolved ? <CircleAlert size={13} /> : na ? <Minus size={13} /> : negative ? <X size={13} /> : <Check size={13} />}</span><span className="an-criterion-text"><strong>{c.title}</strong><small>{unresolved ? 'Требует проверки' : na ? 'Исключён из расчёта · N/A' : c.answer_type === 'flag' ? 'Отдельный флаг' : calculation?.weighted_contribution ? 'Вклад: ' + scoreText(calculation.weighted_contribution) + ' п.п.' : 'Ответ сохранён'}</small></span><span className="an-criterion-value">{unresolved ? '?' : valueText(a.value, c.answer_type)}<ChevronDown size={13} /></span>
          </button>
        })}</div>
        {selectedCriterion && answer && <div className="an-criterion-detail"><div className="an-detail-heading"><span className="an-eyebrow">Критерий {selectedCriterion.id}</span><span>{selectedCriterion.answer_type === 'scale' ? 'Шкала' : selectedCriterion.answer_type === 'flag' ? 'Флаг' : 'Да / Нет'} · вес {selectedCriterion.weight}</span></div><h3 ref={criterionHeading} tabIndex={-1}>{selectedCriterion.title}</h3><p className="an-reason">{answer.reason}</p>
          <dl className="an-calculation-stats"><div><dt>Баллы до перераспределения</dt><dd>{scoreText(criterionCalculation?.earned_points)}</dd></div><div><dt>Эффективный вес</dt><dd>{scoreText(criterionCalculation?.effective_weight)}</dd></div><div><dt>Вклад в итог</dt><dd>{scoreText(criterionCalculation?.weighted_contribution)}{criterionCalculation?.weighted_contribution != null ? ' п.п.' : ''}</dd></div></dl>
          {answer.applicability === 'not_applicable' && <div className="an-note"><strong>N/A: {answer.na_reason}</strong><p>Условие правила: {selectedCriterion.na_condition}</p></div>}
          {answer.review_reasons.length > 0 && <div className="an-note">Нужна проверка: {answer.review_reasons.join('; ')}</div>}
          <div className="an-evidence-buttons">{answer.evidence.map((ref, i) => <button type="button" key={i} className="an-evidence-button" disabled={proofPending} onClick={() => void jump(ref)}><Link2 size={12} />{ref.kind === 'absence' ? 'Отсутствие' : ref.kind === 'context' ? 'Контекст' : 'Реплика'} {(ref.span?.start_message_id ?? ref.message_id).split(':').at(-1)}{ref.span ? '–' + ref.span.end_message_id.split(':').at(-1) : ''}{ref.span && ref.message_id !== ref.span.start_message_id ? ' + ' + ref.message_id.split(':').at(-1) : ''}</button>)}</div>
          {proofError && <p className="an-error" role="alert">{proofError}</p>}
          <div className="an-detail-actions"><button type="button" className="an-button an-primary" disabled={proofPending || !answer.evidence.length} onClick={() => void jump(answer.evidence[0])}><ArrowRight size={15} />{proofPending ? 'Открываем…' : 'Перейти к фрагменту'}</button><button type="button" className="an-button an-secondary" onClick={() => setReview({ criterion: selectedCriterion, answer, appeal: openAppeal })}><MessageSquarePlus size={15} />{openAppeal ? 'Рассмотреть апелляцию' : 'Оспорить оценку'}</button></div>
          <details className="an-rule-details"><summary>Правило и исходный ответ <ChevronDown size={13} /></summary><p>{selectedCriterion.instruction}</p>{selectedCriterion.allow_na && <p><strong>Условие N/A:</strong> {selectedCriterion.na_condition}</p>}{original && <><p><strong>Исходный ответ AI:</strong> {original.decision_status === 'unresolved' ? 'Не определён' : valueText(original.value, selectedCriterion.answer_type)}</p><p>{original.reason}</p></>}{selectedCriterion.levels.length > 0 && <ul>{selectedCriterion.levels.map(l => <li key={l.value}><strong>{l.value}</strong> — {l.description}</li>)}</ul>}</details>
        </div>}
        <details className="an-sections"><summary>Результаты по секциям <ChevronDown size={13} /></summary>{card.checklist.sections.map(section => { const calculated = card.evaluation.final_result?.sections.find(s => s.section_id === section.id); return <div key={section.id}><strong>{section.title}</strong><span>Внутри секции: {scoreText(calculated?.local_percent)}{calculated?.local_percent != null ? '%' : ''}</span><span>Вклад в итог: {scoreText(calculated?.weighted_contribution)}{calculated?.weighted_contribution != null ? ' п.п.' : ''}</span></div> })}</details>
        <div className="an-assessment-foot"><span><ShieldCheck size={13} /> {card.checklist.checklist_version_id}</span><span>Ревизия {card.revision}</span></div>
      </>}
    </section></div>
    {card && <div className="an-bottom-grid"><section className="an-panel an-history"><div className="an-panel-heading"><div><span className="an-eyebrow">Сохранённые изменения</span><h2><History size={18} /> История проверки</h2></div><span className="an-count">{card.evaluation.review_history.length}</span></div>
      {card.evaluation.review_history.length === 0 ? <p className="an-muted">Решений пока нет. Исходная оценка AI сохранена без изменений.</p> : <ol>{card.evaluation.review_history.map(h => { const decision = card.appeals.find(a => a.appeal_id === h.appeal_id)?.decision; const type = card.checklist.criteria.find(c => c.id === h.criterion_id)?.answer_type; const before = decision?.previous_decision_status === 'unresolved' ? 'Не определён' : valueText(h.original_value, type); return <li key={h.appeal_id}><div><span className="an-history-dot"><Check size={12} /></span><strong>{card.checklist.criteria.find(c => c.id === h.criterion_id)?.title ?? h.criterion_id}</strong><small>Ревизия {h.revision}</small></div><p>{h.decision === 'confirmed' ? 'Ответ подтверждён' : before + ' → ' + valueText(h.new_value, type)}</p><p>{h.reason}</p><details><summary>Обращение и доказательства</summary><blockquote>{h.comment}</blockquote>{h.evidence.map((e, i) => <code key={i}>{e.group_id} / {e.conversation_id} / {e.message_id}</code>)}</details></li> })}</ol>}
      {card.appeals.filter(a => a.status === 'open').map(a => <div className="an-pending-appeal" key={a.appeal_id}><MessageSquarePlus size={15} /><div><strong>Открыта апелляция · {card.checklist.criteria.find(c => c.id === a.criterion_id)?.title ?? a.criterion_id}</strong><p>{a.comment}</p></div><button type="button" className="an-text-button" onClick={() => { const c = card.checklist.criteria.find(c => c.id === a.criterion_id); const v = card.final_answers.find(x => x.criterion_id === a.criterion_id); if (c && v) setReview({ criterion: c, answer: v, appeal: a }) }}>Рассмотреть <ArrowRight size={14} /></button></div>)}
    </section><section className="an-panel an-runtime"><div className="an-panel-heading"><div><span className="an-eyebrow">Прозрачность результата</span><h2>Данные оценки</h2></div></div><dl><div><dt>Происхождение</dt><dd>{card.evaluation.generation.mode === 'demo' ? 'Демонстрационный пример' : card.evaluation.generation.mode === 'cache' ? 'Сохранённый запуск' : 'Запуск модели'}</dd></div><div><dt>Модель</dt><dd>{card.evaluation.generation.model_id ?? 'Не указана'}</dd></div><div><dt>Версия чек-листа</dt><dd>{card.checklist.checklist_version_id}</dd></div><div><dt>Завершено</dt><dd>{dateText(card.runtime.finished_at)}</dd></div><div><dt>Время задания</dt><dd>{durationText(card.runtime.started_at, card.runtime.finished_at)}</dd></div><div><dt>Стоимость запуска</dt><dd>Не предоставлена модулем модели</dd></div><div><dt>Для качества модели</dt><dd>{card.evaluation.generation.use_for_model_quality && !isDemo ? 'Да · исходные AI-ответы' : 'Нет'}</dd></div></dl><details><summary>Идентификаторы и настройки</summary><code>{card.evaluation.evaluation_id}</code><code>{card.runtime.job_id}</code><pre>{JSON.stringify(card.runtime.model_config, null, 2)}</pre></details><button type="button" className="an-button an-secondary" disabled={auditPending} onClick={() => void audit()}><ArrowDownToLine size={15} />{auditPending ? 'Готовим отчёт…' : 'Скачать отчёт карточки'}</button>{auditError && <p className="an-error" role="alert">{auditError}</p>}<p className="an-hint">Оригинал, решения и итог с исходной версией правил. Это внутренний отчёт, а не файл сдачи.</p></section></div>}
    {review && card && <ReviewDialog key={review.criterion.id + (review.appeal?.appeal_id ?? '')} card={card} criterion={review.criterion} answer={review.answer} messages={conversation.transcript} existing={review.appeal} onClose={() => { setReview(null); onRefresh() }} onSaved={data => { onSaved(data); setReview(null) }} />}
  </>
}

function AppealsOverview({ group }: { group: string }) {
  const [status, setStatus] = useState<'open' | 'resolved'>('open')
  const appeals = useData<Appeal[]>('/api/appeals?status=' + status)
  const evaluations = useData<EvaluationSummary[]>('/api/evaluations?group_id=' + enc(group))
  const known = new Map(evaluations.data?.map(e => [e.evaluation_id, e]))
  const visible = appeals.data?.filter(a => known.has(a.evaluation_id))
  return <section className="an-panel an-appeals-page"><div className="an-panel-heading"><div><span className="an-eyebrow">Группа {group}</span><h2>Обращения к оценкам</h2></div><label>Статус<select aria-label="Статус" value={status} onChange={e => setStatus(e.target.value as 'open' | 'resolved')}><option value="open">Открытые</option><option value="resolved">Рассмотренные</option></select></label></div>
    {appeals.error || evaluations.error ? <Notice error onRetry={() => { appeals.retry(); evaluations.retry() }}>{appeals.error ?? evaluations.error}</Notice> : !visible ? <Notice>Загружаем апелляции…</Notice> : visible.length === 0 ? <div className="an-appeals-empty"><ClipboardCheck size={40} /><h3>{status === 'open' ? 'Открытых обращений нет' : 'Рассмотренных обращений пока нет'}</h3><p>Апелляцию можно создать у любого критерия сохранённой оценки.</p><a className="an-button an-secondary" href={route('analysis', group)}><ArrowLeft size={15} />К анализу диалога</a></div> : <ul className="an-appeal-list">{visible.map(a => {
      const evaluation = known.get(a.evaluation_id)!
      return <li key={a.appeal_id}><span className={'an-appeal-icon' + (a.status === 'resolved' ? ' an-resolved' : '')}>{a.status === 'resolved' ? <Check size={19} /> : <MessageSquarePlus size={19} />}</span><div><div className="an-appeal-title"><strong>{evaluation.conversation_id}</strong><span>Критерий {a.criterion_id}</span></div><p>{a.comment}</p>{a.decision && <p className="an-appeal-result"><strong>{a.decision.action === 'confirm' ? 'Ответ подтверждён' : 'Ответ изменён на ' + valueText(a.decision.value)}</strong> · {a.decision.reason}</p>}<small>{dateText(a.created_at)} · {evaluation.checklist_version_id}</small></div><a className="an-button an-secondary" href={route('analysis', evaluation.group_id, evaluation.conversation_id, evaluation.evaluation_id)}>Открыть карточку <ArrowRight size={15} /></a></li>
    })}</ul>}
  </section>
}
