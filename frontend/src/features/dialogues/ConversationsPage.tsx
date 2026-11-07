import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Link2, MessagesSquare, Phone, RefreshCw } from 'lucide-react'
import { api, failure, groupPath, people, screenUrl, useData } from './data'
import type { Conversation, Message, Summary } from './data'
import CallRecording from './CallRecording'
import '../checklists/ChecklistsPage.css'
import './ConversationsPage.css'

function Problem({ message, retry }: { message: string; retry: () => void }) {
  return <p className="cv-notice" role="alert">{message} <button className="cv-link" onClick={retry}>Повторить запрос</button></p>
}

function Transcript({ item, path, messageId, onEvidenceLink }: {
  item: Conversation; path: string; messageId: string | null; onEvidenceLink: (id: string) => void
}) {
  const evidence = useData<Message>(messageId ? path + '/messages/' + encodeURIComponent(messageId) : null)
  const selected = !evidence.error && item.transcript.some(t => t.message_id === messageId) ? messageId : null
  const nodes = useRef(new Map<string, HTMLLIElement>())
  function focusMessage(id: string) {
    const node = nodes.current.get(id)
    node?.focus({ preventScroll: true })
    node?.scrollIntoView({ block: 'center' })
  }
  useEffect(() => {
    if (evidence.data && evidence.data.message_id === messageId) focusMessage(evidence.data.message_id)
  }, [evidence.data, messageId])
  return <>
    <div className="cv-heading"><h2>{item.conversation_id}</h2>
      <p>{item.channel === 'voice call' ? 'Звонок' : 'Чат'} · {item.language} · Реплик: {item.message_count}</p></div>
    {item.channel === 'voice call' && (item.has_audio
      ? <CallRecording path={path} messages={item.transcript} evidenceId={selected ?? undefined} onSelect={id => {
        onEvidenceLink(id); focusMessage(id)
      }} />
      : <p className="cv-notice">Для этого звонка нет записи.</p>)}
    {evidence.error && <Problem message={evidence.error} retry={evidence.retry} />}
    {messageId && !evidence.data && !evidence.error && <p role="status">Открываем доказательство…</p>}
    <ol className="cv-turns" aria-label="Реплики диалога">
      {item.transcript.map(turn => <li key={turn.message_id} className={'cv-turn' + (selected === turn.message_id ? ' cv-selected' : '')}
        tabIndex={-1} aria-current={selected === turn.message_id ? 'true' : undefined}
        ref={el => { if (el) nodes.current.set(turn.message_id, el); else nodes.current.delete(turn.message_id) }}>
        <div className="cv-turn-head"><strong>{people[turn.speaker]}</strong><span>{turn.timestamp}</span>
          <a className="cv-link-icon" href={screenUrl(item.group_id, item.conversation_id, turn.message_id)}
            aria-label={'Ссылка на реплику ' + turn.message_id} title={turn.message_id}><Link2 size={16} /></a></div>
        <p>{turn.text}</p>
      </li>)}
    </ol>
  </>
}

function Card({ group, id, message, onEvidenceLink }: {
  group: string; id: string; message: string | null; onEvidenceLink: (id: string) => void
}) {
  const path = groupPath(group) + '/conversations/' + encodeURIComponent(id)
  const card = useData<Conversation>(path)
  if (card.error) return <Problem message={card.error} retry={card.retry} />
  if (!card.data) return <p role="status">Загружаем диалог…</p>
  return <Transcript item={card.data} path={path} messageId={message} onEvidenceLink={onEvidenceLink} />
}

export default function ConversationsPage() {
  const [search, setSearch] = useState(() => window.location.search)
  useEffect(() => {
    const sync = () => setSearch(window.location.search)
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  const query = new URLSearchParams(search)
  const group = query.get('group_id')?.trim() || 'DEV'
  const params = new URLSearchParams()
  for (const key of ['channel', 'language']) {
    const value = query.get(key)
    if (value) params.set(key, value)
  }
  const list = useData<Summary[]>(groupPath(group) + '/conversations?' + params.toString())
  const selected = query.get('conversation_id')
  const [draft, setDraft] = useState(group)
  const [importing, setImporting] = useState(false)
  const [importInfo, setImportInfo] = useState('')
  const [importError, setImportError] = useState('')
  async function importGroup() {
    const target = draft.trim()
    if (!target) return
    setImporting(true); setImportInfo(''); setImportError('')
    try {
      const { data } = await api.post<{ total: number; inserted: number }>(groupPath(target) + '/import', null, { timeout: 65000 })
      if (target !== group) { window.location.assign(screenUrl(target)); return }
      setImportInfo('Диалогов: ' + data.total + '. Добавлено: ' + data.inserted + '.')
      list.retry()
    } catch (error) {
      setImportError(failure(error, 'Не удалось импортировать набор.'))
    } finally { setImporting(false) }
  }
  return <div className="cq-app">
    <header className="cq-header"><a href="/" className="cq-brand"><span className="cq-brand-symbol"><MessagesSquare size={22} /></span>
      <span>CQA<small>Качество диалогов</small></span></a>
      <a className="cq-home-link" href="/"><ArrowLeft size={15} />Рабочее пространство</a></header>
    <main className="cv-page">
      <div className="cv-heading"><h1>Диалоги</h1></div>
      <form className="cq-card cv-controls" action="/conversations" method="get">
        <label className="cq-field">Набор<input name="group_id" value={draft} onChange={e => setDraft(e.target.value)} required maxLength={256} /></label>
        <label className="cq-field">Канал<select name="channel" aria-label="Канал" defaultValue={params.get('channel') || ''}>
          <option value="">Все каналы</option><option value="agent chat">Чаты</option><option value="voice call">Звонки</option></select></label>
        <label className="cq-field">Язык<select name="language" aria-label="Язык" defaultValue={params.get('language') || ''}>
          <option value="">Все языки</option>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label>
        <button className="cq-button cq-button-primary" type="submit">Показать</button>
        <button className="cq-button cq-button-secondary" type="button" onClick={() => void importGroup()} disabled={importing || !draft.trim()}>
          <RefreshCw size={15} />{importing ? 'Импортируем…' : 'Импортировать набор'}</button>
      </form>
      {importInfo && <p className="cv-notice" role="status">{importInfo}</p>}
      {importError && <p className="cv-notice" role="alert">{importError}</p>}
      <div className="cv-columns">
        <nav className="cq-card cv-list" aria-label="Список диалогов">
          <h2>Набор {group}</h2>
          {list.error ? <Problem message={list.error} retry={list.retry} /> :
            !list.data ? <p role="status">Загружаем список…</p> :
            !list.data.length ? <p>По этим фильтрам диалогов нет.</p> :
            <ul>{list.data.map(r => <li key={r.conversation_id}>
              <a className="cv-list-link" aria-current={selected === r.conversation_id ? 'page' : undefined}
                href={screenUrl(group, r.conversation_id)}>{r.channel === 'voice call' ? <Phone size={16} /> : <MessagesSquare size={16} />}
                <span><strong>{r.conversation_id}</strong><small>{r.language} · Реплик: {r.message_count}</small></span></a>
            </li>)}</ul>}
        </nav>
        <section className="cq-card cv-transcript" aria-label="Карточка диалога">
          {selected ? <Card group={group} id={selected} message={query.get('message_id')} onEvidenceLink={id => {
            const url = screenUrl(group, selected, id)
            if (window.location.pathname + window.location.search !== url) {
              window.history.pushState(null, '', url)
              setSearch(window.location.search)
            }
          }} /> : <p>Выберите диалог в списке.</p>}
        </section>
      </div>
    </main>
  </div>
}
