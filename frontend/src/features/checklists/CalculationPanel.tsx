import { useState } from 'react'
import { Calculator, CheckCircle2, ShieldAlert } from 'lucide-react'
import { checklistsApi, errorMessage } from './api'
import type { Answers, CalculatedResult, ChecklistVersion } from './types'

const sample: Answers = { '01': true, '02': null, '03': null, '04': 15, '05': true, '06': 7, '07': true, '08': false }
const decimal = (value: string | null) => value === null ? '—' : Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

export default function CalculationPanel({ version }: { version: ChecklistVersion }) {
  const [values, setValues] = useState<Answers>(() => Object.fromEntries(version.criteria.map(c => [c.id, c.answer_type === 'scale' ? c.levels[c.levels.length - 1].value : c.answer_type === 'boolean'])))
  const [result, setResult] = useState<CalculatedResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const baseline = version.criteria.length === 8 && version.criteria.every((c, i) => {
    const expectedWeights = [10, 15, 20, 20, 15, 15, 5, 0]
    if (c.id !== String(i + 1).padStart(2, '0') || c.weight !== expectedWeights[i]) return false
    const value = sample[c.id]
    if (value === null) return c.allow_na
    if (typeof value === 'number') return c.answer_type === 'scale' && c.levels.some(level => level.value === value && Number(level.normalized_value) === value / (c.id === '04' ? 20 : 10))
    return c.answer_type === (c.id === '08' ? 'flag' : 'boolean')
  })
  async function run(next = values) {
    setBusy(true); setError(''); setResult(null); setValues(next)
    try { setResult(await checklistsApi.calculate(version.checklist_version_id, next)) }
    catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  return <section className="cq-calculator" aria-label="Проверка расчёта">
    <div className="cq-section-intro"><div><h2>Проверка расчёта</h2><p>Ручные ответы по сохранённой версии {version.version}. AI не вызывается.</p></div><Calculator size={24} aria-hidden="true" /></div>
    {baseline && <div className="cq-example-bar"><span>Арифметический пример, не оценка диалога</span><button className="cq-button cq-button-secondary" disabled={busy} onClick={() => void run({ ...sample })}>Пример 85,38</button><button className="cq-button cq-button-secondary" disabled={busy} onClick={() => void run({ ...sample, '04': 20 })}>После пересмотра 93,08</button></div>}
    <fieldset disabled={busy} className="cq-answer-grid"><legend className="cq-sr-only">Ответы по критериям</legend>
      {version.criteria.map(c => <label className="cq-answer-row" key={c.id}><span><small>Критерий {c.id}</small><strong>{c.title}</strong></span><select aria-label={`Ответ по критерию ${c.id}`} value={values[c.id] === null ? 'na' : String(values[c.id])} onChange={event => {
        const raw = event.target.value
        setValues({ ...values, [c.id]: raw === 'na' ? null : c.answer_type === 'scale' ? Number(raw) : raw === 'true' }); setResult(null)
      }}>
        {c.answer_type === 'scale' ? c.levels.map(level => <option key={level.value} value={level.value}>{level.value} — {level.description}</option>) : <><option value="true">{c.answer_type === 'flag' ? 'Да — нарушение выявлено' : 'Да'}</option><option value="false">{c.answer_type === 'flag' ? 'Нет — нарушения нет' : 'Нет'}</option></>}
        {c.allow_na && <option value="na">N/A — неприменим</option>}
      </select>{values[c.id] === null && <p className="cq-help">{c.na_condition}</p>}</label>)}
    </fieldset>
    <button className="cq-button cq-button-primary" disabled={busy} onClick={() => void run()}><Calculator size={17} />{busy ? 'Вычисляем…' : 'Рассчитать итог'}</button>
    {error && <p role="alert" className="cq-error">{error}</p>}
    {result && <div className="cq-result" aria-live="polite">
      <div className="cq-result-summary"><div><span className="cq-eyebrow">Итог по версии {version.version}</span><p className="cq-result-number" data-testid="calculation-total">{result.total === null ? 'Не рассчитывается' : decimal(result.total)}{result.total !== null && <small> / 100</small>}</p></div><div className={`cq-result-flag ${result.gross_error ? 'cq-critical' : ''}`}>{result.gross_error ? <ShieldAlert size={20} /> : <CheckCircle2 size={20} />}Грубая ошибка: {result.gross_error ? 'да' : 'нет'}</div></div>
      <p className="cq-help">Применимый вес: {decimal(result.applicable_weight)}. Баллы до перераспределения: {decimal(result.earned_points)}. {result.total === null && 'Нет применимых критериев с весом.'}</p>
      <div className="cq-table-scroll"><table><caption className="cq-sr-only">Результат расчёта по критериям</caption><thead><tr><th>Критерий</th><th>Ответ</th><th>Вес</th><th>Баллы до N/A</th><th>Вклад в итог</th></tr></thead><tbody>{result.criteria.map(row => <tr key={row.criterion_id}><th scope="row">{row.criterion_id} · {version.criteria.find(c => c.id === row.criterion_id)?.title}</th><td>{row.raw_value === null ? 'N/A' : typeof row.raw_value === 'boolean' ? row.raw_value ? 'Да' : 'Нет' : row.raw_value}</td><td>{row.weight}</td><td>{decimal(row.earned_points)}</td><td>{decimal(row.weighted_contribution)}</td></tr>)}</tbody></table></div>
      <div className="cq-section-results">{result.sections.filter(section => version.criteria.some(c => c.section_id === section.section_id && c.answer_type !== 'flag')).map(section => <div key={section.section_id}><span>{version.sections.find(s => s.id === section.section_id)?.title}</span><strong>{decimal(section.weighted_contribution)}<small> балла в итог</small></strong></div>)}</div>
    </div>}
  </section>
}
