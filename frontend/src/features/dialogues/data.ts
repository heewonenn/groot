import axios from 'axios'
import { useEffect, useState } from 'react'

export interface Summary {
  group_id: string
  conversation_id: string
  channel: 'agent chat' | 'voice call'
  language: string
  message_count: number
  has_audio: boolean
}

export interface Message {
  message_id: string
  speaker: 'client' | 'operator' | 'bot' | 'system'
  text: string
  timestamp: string
}

export interface Conversation extends Summary { transcript: Message[] }
export interface Anchor { message_id: string; start_seconds: number; end_seconds: number }
export interface Alignment { status: string; anchors: Anchor[] }

export const people = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' }
export const api = axios.create({ timeout: 15000 })
export const groupPath = (id: string) => '/api/groups/' + encodeURIComponent(id)

export function screenUrl(group: string, conversation?: string, message?: string) {
  const query = new URLSearchParams(window.location.search)
  query.set('group_id', group)
  if (conversation) query.set('conversation_id', conversation)
  else query.delete('conversation_id')
  if (message) query.set('message_id', message)
  else query.delete('message_id')
  return '/conversations?' + query.toString()
}

export function failure(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    const message = error.response?.data?.error?.message
    if (typeof message === 'string') return message
    if (typeof detail === 'string') return detail
  }
  return fallback
}

type Response<T> = { url: string; data?: T; error?: string }

export function useData<T>(url: string | null, timeout = 15000) {
  const [response, setResponse] = useState<Response<T> | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    api.get<T>(url, { signal: controller.signal, timeout }).then(({ data }) => {
      if (!controller.signal.aborted) setResponse({ url, data })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResponse({ url, error: failure(error, 'Не удалось получить данные. Проверьте подключение к серверу.') })
    })
    return () => controller.abort()
  }, [url, attempt, timeout])
  const retry = () => { setResponse(null); setAttempt(n => n + 1) }
  return { data: response?.url === url ? response?.data : undefined, error: response?.url === url ? response?.error : undefined, retry }
}
