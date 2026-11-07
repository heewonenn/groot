import { useEffect, useState } from 'react'
import axios from 'axios'
import { Activity } from 'lucide-react'
import ChecklistsPage from './features/checklists/ChecklistsPage'
import ConversationsPage from './features/dialogues/ConversationsPage'

function WorkspaceHome() {
  const [status, setStatus] = useState('Checking API…')
  async function checkHealth() {
    setStatus('Checking API…')
    try {
      const { data } = await axios.get<{ status: string }>('/api/health', { timeout: 5000 })
      setStatus(data.status === 'ok' ? 'API connected' : 'Unexpected API response')
    } catch {
      setStatus('API unavailable. Start the backend and retry.')
    }
  }
  useEffect(() => { void checkHealth() }, [])
  return (
    <main className="mx-auto max-w-3xl px-6 py-20">
      <Activity aria-hidden="true" className="mb-6 text-teal-700" size={36} />
      <p className="text-sm font-semibold uppercase tracking-widest text-teal-700">Development workspace</p>
      <h1 className="mt-3 text-4xl font-bold tracking-tight">Hackathon AI</h1>
      <p className="mt-5 text-lg text-slate-600">Your starting point for an evidence-driven AI project.</p>
      <a href="/checklists" className="mt-6 inline-block rounded-lg bg-teal-700 px-4 py-3 font-medium text-white hover:bg-teal-800">Открыть редактор чек-листов →</a>
      <a href="/conversations" className="mt-3 block w-fit rounded-lg px-4 py-2 font-medium text-teal-800 underline">Открыть диалоги →</a>
      <section aria-label="Connection" className="mt-10 rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-xl font-semibold">Backend connection</h2>
        <p role="status" aria-live="polite" className="my-4 text-slate-600">{status}</p>
        <button onClick={() => void checkHealth()} className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-700">Check again</button>
      </section>
      <p className="mt-8 text-sm text-slate-500">Редактор чек-листов и API диалогов подключены. Автоматическая оценка моделью ещё не подключена.</p>
    </main>
  )
}

export default function App() {
  if (window.location.pathname === '/conversations') return <ConversationsPage />
  return window.location.pathname === '/checklists' ? <ChecklistsPage /> : <WorkspaceHome />
}
