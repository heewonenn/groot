import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, BookOpen, Check, ClipboardList, Copy, FileClock, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'
import { checklistsApi, errorMessage } from './api'
import CriterionEditor from './CriterionEditor'
import CalculationPanel from './CalculationPanel'
import type { ChecklistContent, ChecklistDraft, ChecklistSummary, ChecklistVersion, Criterion } from './types'
import './ChecklistsPage.css'

const uid = (prefix: string) => `${prefix}-${crypto.randomUUID().slice(0, 8)}`
function updateLocation(checklist: string, version = 'draft') {
  const url = new URL(window.location.href)
  url.searchParams.set('checklist', checklist)
  if (version === 'draft') url.searchParams.delete('version')
  else url.searchParams.set('version', version)
  window.history.replaceState(null, '', url)
}
function validation(content: ChecklistContent, publish: boolean): string {
  if (!content.title.trim()) return 'Укажите название чек-листа.'
  if (!content.channels.length) return 'Выберите хотя бы один канал.'
  if (content.sections.some(section => !section.title.trim())) return 'Заполните названия секций.'
  if (publish && !content.criteria.length) return 'Добавьте хотя бы один критерий перед сохранением версии.'
  for (const c of content.criteria) {
    if (!c.title.trim() || !c.instruction.trim()) return `Критерий ${c.id}: заполните название и правило оценки.`
    if (c.answer_type !== 'flag' && (!Number.isFinite(c.weight) || c.weight <= 0)) return `Критерий ${c.id}: вес должен быть положительным.`
    if (c.allow_na && !c.na_condition?.trim()) return `Критерий ${c.id}: укажите, когда допустим N/A.`
    if (c.answer_type === 'scale' && (c.levels.length < 2 || new Set(c.levels.map(level => level.value)).size !== c.levels.length || c.levels.some(level => !level.description.trim() || !Number.isFinite(level.value)))) return `Критерий ${c.id}: нужны уникальные ступени с описаниями.`
  }
  return ''
}

export default function ChecklistsPage() {
  const [items, setItems] = useState<ChecklistSummary[]>([])
  const [selected, setSelected] = useState('')
  const [draft, setDraft] = useState<ChecklistDraft | null>(null)
  const [content, setContent] = useState<ChecklistContent | null>(null)
  const [versions, setVersions] = useState<ChecklistVersion[]>([])
  const [view, setView] = useState('draft')
  const [mode, setMode] = useState<'edit' | 'calculate'>('edit')
  const [loadingList, setLoadingList] = useState(true)
  const [loadingEditor, setLoadingEditor] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const [createForm, setCreateForm] = useState<{ copyFrom: string | null } | null>(null)
  const [createTitle, setCreateTitle] = useState('')

  const refreshList = useCallback(async (preferred?: string) => {
    const next = await checklistsApi.list()
    setItems(next)
    setSelected(previous => preferred ?? (next.some(item => item.checklist_id === previous) ? previous : next[0]?.checklist_id ?? ''))
    setLoadingList(false)
  }, [])
  useEffect(() => {
    let active = true
    checklistsApi.list().then(next => {
      if (!active) return
      const requested = new URLSearchParams(window.location.search).get('checklist')
      setItems(next); setSelected(next.some(item => item.checklist_id === requested) ? requested! : next[0]?.checklist_id ?? ''); setLoadingList(false)
    }).catch(failure => { if (active) { setError(errorMessage(failure)); setLoadingList(false) } })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!selected) return
    const url = new URL(window.location.href)
    // Keep a deep link on initial load, but clear another checklist's version.
    if (url.searchParams.get('checklist') !== selected) updateLocation(selected)
  }, [selected])
  useEffect(() => {
    if (!selected) return
    let active = true
    Promise.all([checklistsApi.draft(selected), checklistsApi.versions(selected)]).then(([nextDraft, nextVersions]) => {
      if (!active) return
      const requested = new URLSearchParams(window.location.search).get('version')
      const nextView = nextVersions.find(version => version.checklist_version_id === requested)?.checklist_version_id ?? 'draft'
      setDraft(nextDraft); setContent(nextDraft.content); setVersions(nextVersions); setView(nextView); setLoadingEditor(false)
      updateLocation(selected, nextView)
    }).catch(failure => { if (active) { setError(errorMessage(failure)); setLoadingEditor(false) } })
    return () => { active = false }
  }, [selected, reload])

  const dirty = !!content && !!draft && JSON.stringify(content) !== JSON.stringify(draft.content)
  const readOnly = view !== 'draft'
  const displayed = readOnly ? versions.find(version => version.checklist_version_id === view) ?? content : content
  const calculationVersion = readOnly ? versions.find(version => version.checklist_version_id === view) : versions[0]
  const ready = !loadingEditor && draft?.checklist_id === selected && displayed

  function choose(key: string) {
    if (key === selected || (dirty && !window.confirm('Есть несохранённые изменения. Перейти к другому чек-листу?'))) return
    setSelected(key); setLoadingEditor(true); setError(''); setNotice(''); setMode('edit')
  }
  async function retry() {
    if (dirty && !window.confirm('Загрузить сохранённый черновик и отменить локальные изменения?')) return
    setError(''); setNotice(''); setLoadingList(true); setLoadingEditor(true)
    try { await refreshList(); setReload(value => value + 1) }
    catch (failure) { setError(errorMessage(failure)); setLoadingList(false); setLoadingEditor(false) }
  }
  function updateCriterion(next: Criterion) {
    if (content) setContent({ ...content, criteria: content.criteria.map(c => c.id === next.id ? next : c) })
  }
  function addCriterion() {
    if (!content) return
    setContent({ ...content, criteria: [...content.criteria, { id: uid('c'), title: 'Новый критерий', section_id: content.sections[0].id,
      instruction: '', answer_type: 'boolean', weight: 10, allow_na: false, na_condition: null, levels: [], evidence_required: true, flag_when: null }] })
  }
  async function save(publish: boolean) {
    if (!content || !draft) return
    const issue = validation(content, publish)
    if (issue) { setError(issue); setNotice(''); return }
    const normalized = { ...content, criteria: content.criteria.map(c => ({ ...c, levels: [...c.levels].sort((a, b) => a.value - b.value) })) }
    setBusy(true); setError(''); setNotice('')
    try {
      const nextDraft = await checklistsApi.save(selected, draft.revision, normalized)
      setDraft(nextDraft); setContent(nextDraft.content)
      if (publish) {
        const version = await checklistsApi.publish(selected, nextDraft.revision)
        const [reloaded, history] = await Promise.all([checklistsApi.draft(selected), checklistsApi.versions(selected)])
        setDraft(reloaded); setContent(reloaded.content); setVersions(history)
        setNotice(`Сохранена версия ${version.version}. Предыдущие версии доступны в истории.`)
      } else setNotice('Черновик сохранён. Для применения в оценке сохраните версию.')
      await refreshList()
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  async function create() {
    if (!createForm || !createTitle.trim()) return
    if (dirty && !window.confirm('Создать чек-лист и оставить текущие изменения несохранёнными?')) return
    setBusy(true); setError('')
    try {
      const next = await checklistsApi.create(createTitle.trim(), createForm.copyFrom)
      setCreateForm(null); setCreateTitle(''); setLoadingEditor(true); setMode('edit')
      await refreshList(next.checklist_id)
      setNotice('Создан черновик. Настройте критерии и сохраните первую версию.')
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  return <div className="cq-app">
    <header className="cq-header"><a className="cq-brand" href="/"><span className="cq-brand-symbol"><ClipboardList size={22} /></span><span>CQA<small>Качество диалогов</small></span></a><a className="cq-home-link" href="/"><ArrowLeft size={15} />Рабочее пространство</a></header>
    <div className="cq-layout">
      <aside className="cq-sidebar" aria-label="Список чек-листов">
        <div className="cq-sidebar-heading"><span className="cq-eyebrow">Библиотека правил</span><span className="cq-count">{items.length}</span></div>
        <button className="cq-button cq-button-primary cq-full" disabled={busy} onClick={() => { setCreateForm({ copyFrom: null }); setCreateTitle('') }}><Plus size={18} />Новый чек-лист</button>
        {createForm && <form className="cq-create-form" onSubmit={event => { event.preventDefault(); void create() }}>
          <label className="cq-field">{createForm.copyFrom ? 'Название копии' : 'Название нового чек-листа'}<input autoFocus maxLength={160} value={createTitle} onChange={event => setCreateTitle(event.target.value)} /></label>
          <div className="cq-row"><button className="cq-button cq-button-primary" disabled={busy || !createTitle.trim()}>Создать</button><button type="button" className="cq-button cq-button-quiet" disabled={busy} onClick={() => setCreateForm(null)}>Отмена</button></div>
        </form>}
        <nav className="cq-list" aria-label="Выбор чек-листа">{items.map(item => <button key={item.checklist_id} disabled={busy} className={`cq-list-item ${selected === item.checklist_id ? 'is-selected' : ''}`} aria-current={selected === item.checklist_id ? 'page' : undefined} onClick={() => choose(item.checklist_id)}><span className="cq-list-icon"><ClipboardList size={19} /></span><span><strong>{item.title}</strong><small>{item.channels.map(channel => channel === 'agent chat' ? 'Чат' : 'Звонок').join(' · ')} · {item.version_count ? `v${item.version_count}` : 'Черновик'}{item.has_unpublished_changes ? ' · изменён' : ''}</small></span></button>)}</nav>
        <div className="cq-sidebar-note"><BookOpen size={19} /><strong>Правила с историей</strong><p>Каждая оценка использует сохранённую версию. Изменения не переписывают прошлые результаты.</p></div>
      </aside>
      <main className="cq-main">
        <div className="cq-page-heading"><div><p className="cq-eyebrow">Настройка оценки</p><h1>Чек-листы качества</h1><p>Критерии, шкалы и прозрачный расчёт баллов.</p></div><button className="cq-button cq-button-secondary" disabled={busy || loadingList} onClick={() => void retry()}><RefreshCw size={16} />Обновить</button></div>
        {error && <div className="cq-error" role="alert"><strong>Действие не выполнено</strong><p>{error}</p><button className="cq-button cq-button-secondary" disabled={busy} onClick={() => void retry()}>Повторить загрузку</button></div>}
        {notice && <p className="cq-success" role="status"><Check size={18} />{notice}</p>}
        {loadingList || (selected && loadingEditor) ? <div className="cq-empty" role="status"><span className="cq-loading" />Загружаем правила…</div> : !selected ? <div className="cq-empty"><ClipboardList size={32} /><h2>Пока нет чек-листов</h2><p>Создайте первый чек-лист, чтобы настроить критерии оценки.</p></div> : ready && <>
          <div className="cq-toolbar"><div className="cq-segmented" aria-label="Раздел чек-листа"><button aria-pressed={mode === 'edit'} onClick={() => setMode('edit')}>Редактор</button><button aria-pressed={mode === 'calculate'} onClick={() => setMode('calculate')}>Проверка расчёта</button></div><label className="cq-version-select"><FileClock size={16} />Версия<select aria-label="Версия чек-листа" disabled={busy} value={view} onChange={event => { setView(event.target.value); updateLocation(selected, event.target.value); setError('') }}><option value="draft">Текущий черновик{dirty ? ' • не сохранён' : ''}</option>{versions.map(version => <option key={version.checklist_version_id} value={version.checklist_version_id}>Версия {version.version} — сохранена</option>)}</select></label></div>
          {readOnly && <div className="cq-info"><FileClock size={18} /><span>Сохранённая версия доступна только для просмотра. Для изменений откройте текущий черновик.</span></div>}
          {mode === 'calculate' ? calculationVersion ? <><p className="cq-help">Расчёт использует <strong>{calculationVersion.checklist_version_id}</strong>. Несохранённые изменения черновика не учитываются.</p><CalculationPanel key={calculationVersion.checklist_version_id} version={calculationVersion} /></> : <div className="cq-empty"><h2>Сначала сохраните версию</h2><p>Для воспроизводимого расчёта нужны сохранённые правила.</p><button className="cq-button cq-button-primary" onClick={() => setMode('edit')}>Открыть редактор</button></div> : <>
            <section className="cq-card cq-metadata" aria-label="Настройки чек-листа">
              <fieldset disabled={busy}>
                <legend className="cq-sr-only">Название и каналы</legend>
                <div className="cq-row cq-between">
                  <label className="cq-field cq-grow">Название чек-листа
                    <input disabled={readOnly} value={displayed.title} maxLength={160} onChange={event => content && setContent({ ...content, title: event.target.value })} />
                  </label>
                  <button className="cq-button cq-button-secondary" type="button" disabled={!calculationVersion} onClick={() => {
                    setCreateForm({ copyFrom: calculationVersion?.checklist_version_id ?? null })
                    setCreateTitle(`${displayed.title} — копия`)
                  }}><Copy size={16} />Копия</button>
                </div>
                <div className="cq-row cq-channels"><span>Каналы</span>
                  {(['agent chat', 'voice call'] as const).map(channel => <label className="cq-checkbox" key={channel}>
                    <input type="checkbox" disabled={readOnly} checked={displayed.channels.includes(channel)} onChange={event => content && setContent({ ...content, channels: event.target.checked ? [...content.channels, channel] : content.channels.filter(value => value !== channel) })} />
                    {channel === 'agent chat' ? 'Чаты' : 'Звонки'}
                  </label>)}
                </div>
              </fieldset>
              <div className="cq-stats">
                <span><strong>{displayed.criteria.length}</strong> критериев</span>
                <span><strong>{displayed.criteria.filter(c => c.answer_type !== 'flag').reduce((sum, c) => sum + c.weight, 0)}</strong> сумма весов</span>
                <span><strong>{displayed.criteria.filter(c => c.allow_na).length}</strong> допускают N/A</span>
              </div>
            </section>
            <details className="cq-sections-editor"><summary>Секции чек-листа <span>{displayed.sections.length}</span></summary><fieldset disabled={readOnly || busy}>{displayed.sections.map(section => <div className="cq-row" key={section.id}><label className="cq-field cq-grow"><span className="cq-sr-only">Название секции {section.id}</span><input value={section.title} onChange={event => content && setContent({ ...content, sections: content.sections.map(item => item.id === section.id ? { ...item, title: event.target.value } : item) })} /></label><button className="cq-icon cq-danger" aria-label={`Удалить секцию ${section.title}`} disabled={displayed.sections.length === 1 || displayed.criteria.some(c => c.section_id === section.id)} onClick={() => content && setContent({ ...content, sections: content.sections.filter(item => item.id !== section.id) })}><Trash2 size={16} /></button></div>)}<button className="cq-button cq-button-quiet" disabled={displayed.sections.length >= 20} onClick={() => content && setContent({ ...content, sections: [...content.sections, { id: uid('s'), title: 'Новая секция' }] })}><Plus size={15} />Добавить секцию</button><p className="cq-help">Секцию можно удалить после переноса или удаления её критериев.</p></fieldset></details>
            <div className="cq-criteria-list">{displayed.criteria.map((criterion, index) => <CriterionEditor key={`${view}:${criterion.id}`} criterion={criterion} index={index} count={displayed.criteria.length} readOnly={readOnly || busy} sections={displayed.sections} onChange={updateCriterion} onRemove={() => content && setContent({ ...content, criteria: content.criteria.filter(c => c.id !== criterion.id) })} onMove={offset => {
              if (!content) return
              const next = [...content.criteria]; [next[index], next[index + offset]] = [next[index + offset], next[index]]; setContent({ ...content, criteria: next })
            }} />)}</div>
            {!readOnly && <button className="cq-add-criterion" disabled={busy || displayed.criteria.length >= 100} onClick={addCriterion}><Plus size={19} />Добавить критерий</button>}
            {!readOnly && <div className="cq-save-bar"><span>{dirty ? 'Есть несохранённые изменения' : 'Черновик сохранён'}<small>{draft.base_version_id ? `Последняя версия: ${draft.base_version_id}` : 'Пока нет сохранённых версий'}</small></span><div className="cq-row"><button className="cq-button cq-button-secondary" disabled={busy || !dirty} onClick={() => void save(false)}>Сохранить черновик</button><button className="cq-button cq-button-primary" disabled={busy} onClick={() => void save(true)}><Save size={16} />{busy ? 'Сохраняем…' : 'Сохранить версию'}</button></div></div>}
          </>}
        </>}
      </main>
    </div>
  </div>
}
