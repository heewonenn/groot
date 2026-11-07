import { useEffect, useRef, useState } from 'react'
import { Download, X } from 'lucide-react'
import { useData } from '../dialogues/data'
import { analysisApi, downloadJson, enc, explain } from './api'
import type { JobSummary } from './types'

export default function ExportDialog({ group, onClose }: { group: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [split, setSplit] = useState<'dev' | 'prod'>('dev')
  const [modelMode, setModelMode] = useState('')
  const [jobIds, setJobIds] = useState<string[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const jobs = useData<JobSummary[]>('/api/evaluation-jobs?group_id=' + enc(group))
  const roots = jobs.data?.filter(job => !job.parent_job_id)
  useEffect(() => { dialog.current?.showModal() }, [])
  async function prepare() {
    setPending(true); setError(''); setSuccess(false)
    try {
      const { data } = await analysisApi.post('/api/submissions/prepare', { group_id: group, split, job_ids: jobIds,
        team_name: 'WE ARE GROOT', model_mode: modelMode }, { timeout: 60000 })
      downloadJson(data, 'results.json')
      setSuccess(true)
    } catch (err) { setError(explain(err)) } finally { setPending(false) }
  }
  return <dialog ref={dialog} className="an-dialog" aria-labelledby="an-export-title" onCancel={e => { if (pending) e.preventDefault(); else onClose() }}>
    <div className="an-dialog-heading"><div><span className="an-eyebrow">Приложение PDF · JSON 1.0</span><h2 id="an-export-title">Подготовить results.json</h2></div><button type="button" className="an-icon-button" aria-label="Закрыть экспорт" onClick={onClose} disabled={pending}><X /></button></div>
    <form className="an-dialog-content" onSubmit={e => { e.preventDefault(); void prepare() }}>
      <p>Экспорт исходных результатов AI для команды <strong>WE ARE GROOT</strong>. Решения по апелляциям входят в отдельный отчёт карточки.</p>
      <p className="an-note">Демонстрационные ответы не подходят для официальной сдачи. Все диалоги набора должны быть обработаны; ошибки сохраняются в файле.</p>
      <label>Группа<input value={group} readOnly /></label>
      <div className="an-form-row"><label>Набор<select aria-label="Набор" value={split} onChange={e => setSplit(e.target.value as 'dev' | 'prod')}><option value="dev">DEV — 60 чатов + 5 звонков</option><option value="prod">PROD — 10 чатов</option></select></label>
        <label>Где фактически выполнялась модель<select aria-label="Где фактически выполнялась модель" value={modelMode} onChange={e => setModelMode(e.target.value)} required><option value="" disabled>Укажите режим</option><option value="local">local — локальная модель</option><option value="external_api">external_api — внешний API</option><option value="hybrid">hybrid — оба варианта</option></select></label></div>
      <p className="an-hint">Режим модели подтверждается настройками реального запуска; live/cache/demo обозначает происхождение ответа.</p>
      <fieldset className="an-export-jobs"><legend>Задания для полного набора</legend>
        {jobs.error ? <p className="an-error" role="alert">{jobs.error} <button type="button" onClick={jobs.retry}>Повторить</button></p> : !roots ? <p role="status">Загружаем задания…</p> : roots.length === 0 ? <p>Заданий для этой группы пока нет.</p> : roots.map(job => <label key={job.job_id}><input type="checkbox" checked={jobIds.includes(job.job_id)} onChange={e => setJobIds(e.target.checked ? [...jobIds, job.job_id] : jobIds.filter(id => id !== job.job_id))} /><span><code>{job.job_id}</code><small>{job.checklist_version_id} · {job.status}</small></span></label>)}
      </fieldset>
      <p className="an-hint">Выбираются исходные задания. Сохранённые повторные попытки сервер добавит к ним автоматически.</p>
      {error && <p className="an-error" role="alert">{error}</p>}
      {success && <p className="an-success" role="status">Файл подготовлен. Сохраните его в results/{split}/results.json локального проекта.</p>}
      <p className="an-hint">Загрузка файла не отправляет изменения в GitHub.</p>
      <div className="an-dialog-footer"><button type="button" className="an-button an-secondary" onClick={onClose} disabled={pending}>Закрыть</button><button className="an-button an-primary" disabled={pending || !modelMode || jobIds.length === 0}><Download size={16} />{pending ? 'Проверяем набор…' : 'Проверить и скачать'}</button></div>
    </form>
  </dialog>
}
