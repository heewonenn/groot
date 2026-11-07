import type { ModelRun } from './modelTypes'

export type Value = boolean | number | null
export interface Evidence {
  group_id: string
  conversation_id: string
  message_id: string
  kind: 'support' | 'absence' | 'context'
  span: { start_message_id: string; end_message_id: string } | null
}
export interface Answer {
  criterion_id: string
  decision_status: 'evaluated' | 'unresolved'
  applicability: 'applicable' | 'not_applicable' | 'undetermined'
  value: Value
  reason: string
  evidence: Evidence[]
  na_reason: string | null
  review_reasons: string[]
  analysis_scope: string[]
  knowledge_references: { source_version_id: string; fragment_id: string }[]
}
export interface Criterion {
  id: string
  title: string
  section_id: string
  instruction: string
  answer_type: 'boolean' | 'scale' | 'flag'
  weight: number
  allow_na: boolean
  na_condition: string | null
  evidence_required: boolean
  flag_when: boolean | null
  levels: { value: number; description: string; normalized_value: string }[]
}
export interface Checklist {
  checklist_id: string
  checklist_version_id: string
  title: string
  version: number
  channels: string[]
  sections: { id: string; title: string }[]
  criteria: Criterion[]
}
export interface Calculation {
  total: string | null
  gross_error: boolean
  applicable_weight: string
  earned_points: string
  criteria: { criterion_id: string; raw_value: Value; applicability: string; weight: number;
    normalized_value: string | null; earned_points: string | null;
    effective_weight: string | null; weighted_contribution: string | null }[]
  sections: { section_id: string; applicable_weight: string;
    weighted_contribution: string | null; local_percent: string | null }[]
}
export interface Generation {
  mode: 'live' | 'cache' | 'demo'
  origin: string
  model_id: string | null
  is_ground_truth: boolean
  use_for_model_quality: boolean
  note: string
}
export interface ReviewHistory {
  appeal_id: string
  criterion_id: string
  checklist_version_id: string
  comment: string
  decision: 'confirmed' | 'changed'
  original_value: Value
  new_value: Value
  reason: string
  revision: number
  evidence: { group_id: string; conversation_id: string; message_id: string }[]
}
export interface Evaluation {
  evaluation_id: string
  group_id: string
  conversation_id: string
  checklist_id: string
  checklist_version_id: string
  status: 'completed' | 'partial' | 'failed'
  generation: Generation
  model_result: { criteria: Answer[] } | null
  original_result: Calculation | null
  final_result: Calculation | null
  review_history: ReviewHistory[]
}
export interface Decision {
  action: 'confirm' | 'change'
  value: Value
  applicability: 'applicable' | 'not_applicable'
  reason: string
  na_reason: string | null
  evidence: Evidence[]
  created_at: string
  previous_value: Value
  previous_applicability: string
  previous_decision_status: string
}
export interface Appeal {
  appeal_id: string
  evaluation_id: string
  criterion_id: string
  comment: string
  status: 'open' | 'resolved'
  created_at: string
  decision: Decision | null
}
export interface Card {
  evaluation: Evaluation
  checklist: Checklist
  final_answers: Answer[]
  revision: number
  final_status: 'completed' | 'partial' | 'failed'
  appeals: Appeal[]
  runtime: { job_id: string; started_at: string | null; finished_at: string | null;
    model_config: Record<string, unknown>; model_run?: ModelRun | null }
}
export interface EvaluationSummary {
  evaluation_id: string
  group_id: string
  conversation_id: string
  checklist_version_id: string
  status: string
  generation: Generation
  revision: number
  original_total: string | null
  final_total: string | null
  job_id: string
  created_at: string
}
export interface JobSummary {
  job_id: string
  parent_job_id: string | null
  group_id: string
  checklist_version_id: string
  status: string
  created_at: string
  counts?: Record<string, number>
}
export interface DecisionPayload {
  action: 'confirm' | 'change'
  value: Value
  applicability: 'applicable' | 'not_applicable'
  reason: string
  na_reason: string | null
  evidence: Evidence[]
  expected_revision: number
}
