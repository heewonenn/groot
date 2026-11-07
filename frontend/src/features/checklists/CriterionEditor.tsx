import { useState } from 'react'
import { ArrowDown, ArrowUp, ChevronDown, Plus, Trash2 } from 'lucide-react'
import type { AnswerType, Criterion, ScaleLevel } from './types'

interface Props {
  criterion: Criterion; index: number; count: number; readOnly: boolean
  sections: { id: string; title: string }[]
  onChange: (criterion: Criterion) => void; onRemove: () => void; onMove: (offset: number) => void
}

function normalize(levels: ScaleLevel[]): ScaleLevel[] {
  const values = levels.map(level => level.value)
  const min = Math.min(...values), max = Math.max(...values)
  return levels.map(level => ({ ...level, normalized_value: max > min ? String(Number(((level.value - min) / (max - min)).toFixed(12))) : '0' }))
}

export default function CriterionEditor({ criterion: c, index, count, readOnly, sections, onChange, onRemove, onMove }: Props) {
  const [open, setOpen] = useState(index === 0 || !c.instruction)
  const update = (patch: Partial<Criterion>) => onChange({ ...c, ...patch })
  function changeType(type: AnswerType) {
    update({ answer_type: type, weight: type === 'flag' ? 0 : c.weight || 10, flag_when: type === 'flag' ? true : null,
      levels: type === 'scale' ? [{ value: 0, description: 'Требование не выполнено', normalized_value: '0' }, { value: 10, description: 'Требование выполнено полностью', normalized_value: '1' }] : [],
    })
  }
  return <details className={`cq-criterion ${c.answer_type === 'flag' ? 'cq-flag-card' : ''}`} open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <span className="cq-criterion-number">{String(index + 1).padStart(2, '0')}</span>
      <span className="cq-criterion-heading"><strong>{c.title || 'Новый критерий'}</strong><small>{sections.find(section => section.id === c.section_id)?.title}</small></span>
      <span className="cq-pill">{c.answer_type === 'flag' ? 'Флаг' : `Вес ${c.weight}`}</span>
      {c.allow_na && <span className="cq-na-tag">N/A</span>}<ChevronDown size={16} aria-hidden="true" />
    </summary>
    <fieldset disabled={readOnly} className="cq-criterion-body">
      <legend className="cq-sr-only">Критерий {c.id}</legend>
      <div className="cq-row cq-criterion-actions"><span className="cq-muted">ID {c.id}</span><div className="cq-row">
        <button type="button" className="cq-icon" disabled={index === 0} aria-label={`Поднять критерий ${c.id}`} onClick={() => onMove(-1)}><ArrowUp size={16} /></button>
        <button type="button" className="cq-icon" disabled={index === count - 1} aria-label={`Опустить критерий ${c.id}`} onClick={() => onMove(1)}><ArrowDown size={16} /></button>
        <button type="button" className="cq-icon cq-danger" aria-label={`Удалить критерий ${c.id}`} onClick={onRemove}><Trash2 size={16} /></button>
      </div></div>
      <label className="cq-field">Название<input aria-label={`Название критерия ${c.id}`} value={c.title} maxLength={160} onChange={event => update({ title: event.target.value })} /></label>
      <div className="cq-grid-three">
        <label className="cq-field">Секция<select aria-label={`Секция критерия ${c.id}`} value={c.section_id} onChange={event => update({ section_id: event.target.value })}>{sections.map(section => <option key={section.id} value={section.id}>{section.title}</option>)}</select></label>
        <label className="cq-field">Тип ответа<select aria-label={`Тип ответа ${c.id}`} value={c.answer_type} onChange={event => changeType(event.target.value as AnswerType)}><option value="boolean">Да / Нет</option><option value="scale">Числовая шкала</option><option value="flag">Флаг нарушения</option></select></label>
        <label className="cq-field">Вес<input aria-label={`Вес критерия ${c.id}`} type="number" min={0} max={100000} step="any" disabled={readOnly || c.answer_type === 'flag'} value={c.weight} onChange={event => update({ weight: Number(event.target.value) })} /></label>
      </div>
      <label className="cq-field">Правило оценки<textarea aria-label={`Правило критерия ${c.id}`} rows={4} maxLength={12000} value={c.instruction} placeholder="Какое действие должен выполнить оператор? На что опираться при оценке?" onChange={event => update({ instruction: event.target.value })} /></label>
      {c.answer_type === 'scale' && <div className="cq-scale">
        <div className="cq-row cq-between"><h4>Ступени шкалы</h4><span className="cq-muted">Значение и его смысл</span></div>
        {c.levels.map((level, levelIndex) => <div className="cq-scale-row" key={levelIndex}>
          <label className="cq-field">Значение<input type="number" step="any" aria-label={`Значение ступени ${levelIndex + 1} критерия ${c.id}`} value={level.value} onChange={event => update({ levels: normalize(c.levels.map((item, i) => i === levelIndex ? { ...item, value: Number(event.target.value) } : item)) })} /></label>
          <label className="cq-field">Описание<textarea rows={2} aria-label={`Описание ступени ${levelIndex + 1} критерия ${c.id}`} value={level.description} onChange={event => update({ levels: c.levels.map((item, i) => i === levelIndex ? { ...item, description: event.target.value } : item) })} /></label>
          <span className="cq-scale-ratio">{Math.round(Number(level.normalized_value) * 10000) / 100}% веса</span>
          <button type="button" className="cq-icon cq-danger" aria-label={`Удалить ступень ${levelIndex + 1} критерия ${c.id}`} disabled={c.levels.length <= 2} onClick={() => update({ levels: normalize(c.levels.filter((_, i) => i !== levelIndex)) })}><Trash2 size={16} /></button>
        </div>)}
        <button type="button" className="cq-button cq-button-quiet" disabled={c.levels.length >= 30} onClick={() => update({ levels: normalize([...c.levels, { value: Math.max(...c.levels.map(level => level.value)) + 5, description: '', normalized_value: '1' }]) })}><Plus size={15} />Добавить ступень</button>
        <p className="cq-help">При изменении значений вклад пересчитывается от 0% у минимальной ступени до 100% у максимальной. Итог рассчитывает сервер.</p>
      </div>}
      <label className="cq-checkbox"><input type="checkbox" checked={c.allow_na} onChange={event => update({ allow_na: event.target.checked, na_condition: event.target.checked ? '' : null })} />Разрешить N/A — критерий неприменим</label>
      {c.allow_na && <label className="cq-field">Когда допустим N/A<textarea rows={2} aria-label={`Условие N/A ${c.id}`} value={c.na_condition ?? ''} placeholder="Укажите конкретное условие неприменимости" onChange={event => update({ na_condition: event.target.value })} /></label>}
      {c.answer_type === 'flag' && <p className="cq-help">«Да» отмечает нарушение. Флаг сохраняется отдельно и не меняет числовой итог.</p>}
    </fieldset>
  </details>
}
