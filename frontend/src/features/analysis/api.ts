import axios from 'axios'
import type { Value } from './types'
export const analysisApi = axios.create({ timeout: 20000 })

export function valueText(value: Value, type?: string) {
  if (value === null) return 'N/A'
  if (typeof value === 'boolean') return type === 'flag' ? (value ? 'Есть нарушение' : 'Нет нарушения') : value ? 'Да' : 'Нет'
  return String(value)
}

export function explain(error: unknown, fallback = 'Не удалось выполнить запрос. Проверьте подключение и повторите.') {
  if (axios.isAxiosError(error)) {
    const data: unknown = error.response?.data
    if (data && typeof data === 'object') {
      const response = data as { detail?: unknown; error?: { message?: unknown } }
      if (typeof response.error?.message === 'string') return response.error.message
      if (typeof response.detail === 'string') return response.detail
      if (Array.isArray(response.detail)) return 'Проверьте значения полей и обязательные доказательства.'
    }
    if (error.code === 'ECONNABORTED') return 'Сервер не ответил вовремя. Можно безопасно повторить запрос.'
  }
  return fallback
}

export function freshKey() {
  return globalThis.crypto.randomUUID()
}

export function downloadJson(data: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const enc = encodeURIComponent
export const route = (view: string, group?: string, conversation?: string, evaluation?: string) => {
  const q = new URLSearchParams({ view })
  if (group) q.set('group_id', group)
  if (conversation) q.set('conversation_id', conversation)
  if (evaluation) q.set('evaluation_id', evaluation)
  return '/?' + q.toString()
}
