import { useEffect, useRef, useState } from 'react'
import { Headphones } from 'lucide-react'
import { useData } from './data'
import type { Alignment, Anchor, Message } from './data'

function time(seconds: number) {
  return Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0')
}

export default function CallRecording({ path, messages, evidenceId, onSelect }: {
  path: string; messages: Message[]; evidenceId?: string; onSelect: (id: string) => void
}) {
  const player = useRef<HTMLAudioElement>(null)
  const pending = useRef<number | null>(null)
  const requested = useRef<string | null>(null)
  const [audioError, setAudioError] = useState(false)
  const alignment = useData<Alignment>(path + '/audio-anchors', 75000)
  const anchors = alignment.data?.anchors.filter(a => messages.some(m => m.message_id === a.message_id)) ?? []
  const evidenceStart = anchors.find(a => a.message_id === evidenceId)?.start_seconds
  useEffect(() => {
    if (evidenceStart === undefined) { pending.current = null; return }
    if (requested.current === evidenceId) { requested.current = null; return }
    pending.current = evidenceStart
    if (player.current && player.current.readyState >= 1) {
      player.current.currentTime = evidenceStart
      pending.current = null
    }
  }, [evidenceId, evidenceStart])

  function seek(anchor: Anchor) {
    const audio = player.current
    if (!audio) return
    requested.current = anchor.message_id
    onSelect(anchor.message_id)
    pending.current = anchor.start_seconds
    if (audio.readyState >= 1) {
      audio.currentTime = anchor.start_seconds
      pending.current = null
    }
    if (audioError) {
      setAudioError(false)
      audio.load()
    }
    void audio.play().catch(() => { if (audio.error) setAudioError(true) })
  }

  return <section className="cq-card cv-recording" aria-label="Запись звонка">
    <h3><Headphones size={16} aria-hidden="true" /> Запись звонка</h3>
    <audio ref={player} controls preload="none" src={path + '/audio'} aria-label="Прослушать разговор"
      onError={() => setAudioError(true)} onLoadedMetadata={() => {
        if (player.current && pending.current !== null) {
          player.current.currentTime = pending.current
          pending.current = null
        }
      }} />
    {audioError && <p role="alert">Запись не загрузилась. <button className="cv-link" onClick={() => {
      setAudioError(false); player.current?.load()
    }}>Загрузить запись снова</button></p>}
    {alignment.error ? <p role="alert">Не удалось получить позиции реплик. <button className="cv-link" onClick={alignment.retry}>Повторить</button></p> :
      !alignment.data ? <p role="status">Загружаем позиции реплик…</p> :
      anchors.length === 0 ? <p>Позиции реплик в записи ещё не подтверждены.</p> :
      <div className="cv-anchors"><span>Проверенные фрагменты</span>{anchors.map(a =>
        <button type="button" key={a.message_id} onClick={() => seek(a)} title={a.message_id}
          aria-label={'Перейти к фрагменту ' + time(a.start_seconds) + ', ' + a.message_id}>
          {time(a.start_seconds)}–{time(a.end_seconds)}
        </button>)}</div>}
    {evidenceId && alignment.data && <p>{evidenceStart === undefined
      ? 'Для выбранной реплики позиция в записи не подтверждена.'
      : 'Позиция выбранной реплики: ' + time(evidenceStart) + '.'}</p>}
  </section>
}
