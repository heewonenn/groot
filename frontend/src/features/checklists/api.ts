import axios from 'axios'
import type { Answers, CalculatedResult, ChecklistContent, ChecklistDraft, ChecklistSummary, ChecklistVersion } from './types'

const client = axios.create({ baseURL: '/api', timeout: 10000 })
const id = encodeURIComponent
export const checklistsApi = {
  list: async () => (await client.get<ChecklistSummary[]>('/checklists')).data,
  draft: async (key: string) => (await client.get<ChecklistDraft>(`/checklists/${id(key)}/draft`)).data,
  versions: async (key: string) => (await client.get<ChecklistVersion[]>(`/checklists/${id(key)}/versions`)).data,
  create: async (title: string, copyFrom: string | null) => (await client.post<ChecklistDraft>('/checklists', { title, copy_from_version_id: copyFrom })).data,
  save: async (key: string, revision: number, content: ChecklistContent) => (await client.patch<ChecklistDraft>(`/checklists/${id(key)}/draft`, { expected_revision: revision, content })).data,
  publish: async (key: string, revision: number) => (await client.post<ChecklistVersion>(`/checklists/${id(key)}/versions`, { expected_revision: revision })).data,
  calculate: async (version: string, values: Answers) => (await client.post<{ checklist_version_id: string; result: CalculatedResult }>(`/checklist-versions/${id(version)}/calculate`, {
    answers: Object.entries(values).map(([criterion_id, value]) => ({ criterion_id, value, applicability: value === null ? 'not_applicable' : 'applicable', decision_status: 'evaluated' })),
  })).data.result,
}

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const body = error.response?.data?.error as { message?: string; details?: { field: string; message: string }[] } | undefined
    if (body?.message) return [body.message, ...(body.details?.slice(0, 3).map(item => `${item.field}: ${item.message}`) ?? [])].join(' ')
    return 'Не удалось связаться с API. Проверьте запуск сервера и повторите запрос.'
  }
  return 'Не удалось выполнить действие. Повторите запрос.'
}
