import type { Message } from '../dialogues/data'
import type { Evidence } from './types'

export interface ModelStatus {
  configured: boolean
  ready: boolean
  reason: string | null
  provider: string | null
  model: string | null
  prompt_version: string
  audio_analysed: boolean
  knowledge_version: string | null
}

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
}

export interface ModelAttempt {
  number: number
  status: string
  error_code: string | null
  usage: TokenUsage | null
  resolved_model: string | null
  elapsed_ms: number | null
}

export interface ModelRun {
  evaluation_id: string
  group_id: string
  conversation_id: string
  checklist_version_id: string
  status: string
  generation_mode: 'live' | 'cache' | 'demo'
  error_code: string | null
  started_at: string
  finished_at: string | null
  provider: string
  configured_model: string
  resolved_model: string | null
  provider_parameters?: Record<string, unknown>
  prompt_version: string
  prompt_hash?: string
  schema_hash?: string
  knowledge_version: string | null
  knowledge_hash?: string | null
  settings?: Record<string, unknown>
  input_fingerprint?: string | null
  attempts: ModelAttempt[]
  elapsed_ms: number | null
  token_usage: TokenUsage | null
  estimated_cost_usd: number | null
  cost_basis?: string
  audio_analysed: boolean
  context_token_limit?: number | null
  context_budget_used?: number | null
}

export interface SnapshotEvidence {
  criterion_id: string
  reference: Evidence
  messages: Message[]
}
