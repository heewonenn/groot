import { useEffect, useRef, useState } from 'react'
import { Check, MessageSquarePlus, Plus, X } from 'lucide-react'
import type { Message } from '../dialogues/data'
import { analysisApi, enc, explain, freshKey, valueText } from './api'
import type { Answer, Appeal, Card, Criterion, DecisionPayload, Evidence, Value } from './types'

function EvidenceEditor({ evidence, onChange, messages, group, conversation }: {
  evidence: Evidence[]; onChange: (refs: Evidence[]) => void; messages: Message[]; group: string; conversation: string
}) {
  const [messageId, setMessageId] = useState(messages[0]?.message_id ?? '')
  const [endId, setEndId] = useState('')
  const [kind, setKind] = useState<Evidence['kind']>('support')
  const start = messages.findIndex(m => m.message_id === messageId)
  const end = messages.findIndex(m => m.message_id === endId)
  function add() {
    if (!messageId || (endId && end < start)) return
    const ref: Evidence = { group_id: group, conversation_id: conversation, message_id: messageId, kind,
      span: endId ? { start_message_id: messageId, end_message_id: endId } : null }
    if (!evidence.some(e => JSON.stringify(e) === JSON.stringify(ref))) onChange([...evidence, ref])
  }
  return <fieldset className="an-evidence-editor"><legend>Доказательства в исходном диалоге</legend>
    <p className="an-hint">Для отсутствующего действия укажите проверенный фрагмент и тип «Отсутствие».</p>
    <div className="an-form-row"><label>Начальная реплика<select aria-label="Начальная реплика" value={messageId} onChange={e => setMessageId(e.target.value)}>
      {messages.map((m, i) => <option key={m.message_id} value={m.message_id}>{i + 1}. {m.speaker} · {m.timestamp} · {m.text.slice(0, 70)}</option>)}
    </select></label><label>Конец диапазона<select aria-label="Конец диапазона" value={endId} onChange={e => setEndId(e.target.value)}>
      <option value="">Только одна реплика</option>{messages.map((m, i) => <option key={m.message_id} value={m.message_id} disabled={i < start}>{i + 1}. {m.speaker} · {m.timestamp}</option>)}
    </select></label></div>
    <div className="an-form-row"><label>Тип доказательства<select aria-label="Тип доказательства" value={kind} onChange={e => setKind(e.target.value as Evidence['kind'])}>
      <option value="support">Подтверждение</option><option value="absence">Отсутствие</option><option value="context">Контекст</option>
    </select></label><button type="button" className="an-button an-secondary" onClick={add} disabled={!messageId || Boolean(endId && end < start)}><Plus size={15} /> Добавить фрагмент</button></div>
    {evidence.length === 0 ? <p className="an-hint">Фрагменты пока не добавлены.</p> : <ul className="an-evidence-list">{evidence.map((e, i) => <li key={JSON.stringify(e)}>
      <span><strong>{e.kind === 'absence' ? 'Отсутствие' : e.kind === 'context' ? 'Контекст' : 'Подтверждение'}</strong><code>{e.message_id}{e.span ? ' → ' + e.span.end_message_id : ''}</code></span>
      <button type="button" className="an-icon-button" onClick={() => onChange(evidence.filter((_, n) => n !== i))} aria-label={'Убрать доказательство ' + (i + 1)}><X size={16} /></button>
    </li>)}</ul>}
  </fieldset>
}

export default function ReviewDialog({ card, criterion, answer, messages, existing, onClose, onSaved }: {
  card: Card; criterion: Criterion; answer: Answer; messages: Message[]; existing?: Appeal;
  onClose: () => void; onSaved: (card: Card) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [appeal, setAppeal] = useState<Appeal | undefined>(existing)
  const [comment, setComment] = useState('')
  const [action, setAction] = useState<'confirm' | 'change'>(answer.decision_status === 'unresolved' ? 'change' : 'confirm')
  const [encoded, setEncoded] = useState(answer.applicability === 'not_applicable' ? 'na' : typeof answer.value === 'boolean' ? String(answer.value) : answer.value === null ? '' : String(answer.value))
  const [reason, setReason] = useState('')
  const [naReason, setNaReason] = useState(answer.na_reason ?? '')
  const [evidence, setEvidence] = useState<Evidence[]>(answer.evidence)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const receipt = useRef<{ body: string; key: string } | null>(null)
  const original = card.evaluation.model_result?.criteria.find(a => a.criterion_id === criterion.id)
  useEffect(() => { dialog.current?.showModal() }, [])
  const changing = action === 'change'
  const selected = changing ? encoded : answer.applicability === 'not_applicable' ? 'na' : String(answer.value)
  const nextValue: Value = selected === 'na' ? null : criterion.answer_type === 'scale' ? Number(selected) : selected === 'true'
  const unchanged = nextValue === answer.value && (selected === 'na' ? 'not_applicable' : 'applicable') === answer.applicability && answer.decision_status !== 'unresolved'

  async function openAppeal() {
    setPending(true); setError('')
    try {
      const { data } = await analysisApi.post<Appeal>(`/api/evaluations/${enc(card.evaluation.evaluation_id)}/appeals`, { criterion_id: criterion.id, comment: comment.trim() })
      setAppeal(data)
    } catch (err) { setError(explain(err)) } finally { setPending(false) }
  }
  async function saveDecision() {
    if (!appeal) return
    const payload: DecisionPayload = { action, value: nextValue, applicability: selected === 'na' ? 'not_applicable' : 'applicable',
      reason: reason.trim(), na_reason: selected === 'na' ? naReason.trim() : null, evidence, expected_revision: card.revision }
    const body = JSON.stringify(payload)
    if (receipt.current?.body !== body) receipt.current = { body, key: freshKey() }
    setPending(true); setError('')
    try {
      const { data } = await analysisApi.post<Card>(`/api/appeals/${enc(appeal.appeal_id)}/decision`, payload,
        { headers: { 'Idempotency-Key': receipt.current.key } })
      onSaved(data)
    } catch (err) { setError(explain(err)) } finally { setPending(false) }
  }
  return <dialog ref={dialog} className="an-dialog" aria-labelledby="an-review-title" onCancel={e => { if (pending) e.preventDefault(); else onClose() }}>
    <div className="an-dialog-heading"><div><span className="an-eyebrow">Апелляция · {criterion.id}</span><h2 id="an-review-title">{appeal ? 'Решение проверяющего' : 'Оспорить оценку'}</h2></div>
      <button type="button" className="an-icon-button" onClick={onClose} disabled={pending} aria-label="Закрыть апелляцию"><X /></button></div>
    <div className="an-dialog-content"><h3>{criterion.title}</h3><p className="an-hint">{criterion.instruction}</p>
      <div className="an-original-value">Текущий ответ: <strong>{answer.decision_status === 'unresolved' ? 'Требует проверки' : valueText(answer.value, criterion.answer_type)}</strong><span>Версия {card.checklist.checklist_version_id} · ревизия {card.revision}</span></div>
      {original && <div className="an-ai-original"><strong>Исходный ответ AI: {original.decision_status === 'unresolved' ? 'Не определён' : valueText(original.value, criterion.answer_type)}</strong><p>{original.reason}</p>{original.na_reason && <p>N/A: {original.na_reason}</p>}</div>}
      {!appeal ? <form onSubmit={e => { e.preventDefault(); void openAppeal() }}>
        <label>Комментарий к апелляции<textarea value={comment} onChange={e => setComment(e.target.value)} required maxLength={12000} rows={5} placeholder="Что требует пересмотра и почему?" autoFocus /></label>
        {error && <p className="an-error" role="alert">{error}</p>}
        <div className="an-dialog-footer"><button type="button" className="an-button an-secondary" onClick={onClose} disabled={pending}>Отмена</button><button className="an-button an-primary" disabled={pending || !comment.trim()}><MessageSquarePlus size={16} /> {pending ? 'Сохраняем…' : 'Сохранить обращение'}</button></div>
      </form> : <form onSubmit={e => { e.preventDefault(); void saveDecision() }}>
        <blockquote className="an-appeal-comment"><span>Комментарий автора</span>{appeal.comment}</blockquote>
        <fieldset className="an-choice"><legend>Решение</legend><label><input type="radio" name="action" value="confirm" checked={action === 'confirm'} onChange={() => setAction('confirm')} disabled={answer.decision_status === 'unresolved'} /> Подтвердить ответ</label><label><input type="radio" name="action" value="change" checked={changing} onChange={() => setAction('change')} /> Изменить ответ</label></fieldset>
        {changing && <label>Новый ответ<select aria-label="Новый ответ" value={encoded} onChange={e => setEncoded(e.target.value)} required>
          <option value="" disabled>Выберите значение</option>
          {criterion.answer_type === 'scale' ? criterion.levels.map(level => <option key={level.value} value={String(level.value)}>{level.value} — {level.description}</option>) : <><option value="true">{criterion.answer_type === 'flag' ? 'Есть грубая ошибка' : 'Да'}</option><option value="false">{criterion.answer_type === 'flag' ? 'Нет грубой ошибки' : 'Нет'}</option></>}
          {criterion.allow_na && <option value="na">N/A — критерий неприменим</option>}
        </select></label>}
        {selected === 'na' && <><p className="an-note">Условие N/A: {criterion.na_condition}</p><label>Почему выполнено условие N/A<textarea value={naReason} onChange={e => setNaReason(e.target.value)} required maxLength={12000} rows={2} /></label></>}
        <label>Обоснование решения<textarea value={reason} onChange={e => setReason(e.target.value)} required maxLength={12000} rows={3} placeholder="Объясните решение по правилу исходной версии чек-листа." /></label>
        <EvidenceEditor evidence={evidence} onChange={setEvidence} messages={messages} group={card.evaluation.group_id} conversation={card.evaluation.conversation_id} />
        {changing && unchanged && <p className="an-note">Ответ совпадает с текущим. Выберите «Подтвердить» или другое значение.</p>}
        {error && <p className="an-error" role="alert">{error}{error.includes('409') ? ' Обновите карточку перед новым решением.' : ''}</p>}
        <p className="an-hint">Исходный ответ AI сохраняется. Итог пересчитает общий модуль участника №1 по этой же версии.</p>
        <div className="an-dialog-footer"><button type="button" className="an-button an-secondary" onClick={onClose} disabled={pending}>Закрыть</button><button className="an-button an-primary" disabled={pending || !reason.trim() || (changing && (!encoded || unchanged)) || (selected === 'na' && !naReason.trim()) || (criterion.evidence_required && evidence.length === 0)}><Check size={16} /> {pending ? 'Сохраняем решение…' : 'Применить решение'}</button></div>
      </form>}
    </div>
  </dialog>
}
