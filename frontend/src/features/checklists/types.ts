export type Channel = 'agent chat' | 'voice call'
export type AnswerType = 'boolean' | 'scale' | 'flag'
export interface ScaleLevel { value: number; description: string; normalized_value: string }
export interface Criterion {
  id: string; title: string; section_id: string; instruction: string
  answer_type: AnswerType; weight: number; allow_na: boolean; na_condition: string | null
  levels: ScaleLevel[]; evidence_required: boolean; flag_when: boolean | null
}
export interface ChecklistContent {
  title: string; channels: Channel[]; source: string
  sections: { id: string; title: string }[]; criteria: Criterion[]
  scoring: {
    total_max: 100; rounding: 'ROUND_HALF_UP'; decimal_places: 2
    exclude_na_from_denominator: true; flag_affects_total: false
  }
}
export interface ChecklistVersion extends ChecklistContent {
  kind: 'checklist_version'; schema_version: '1.0'
  checklist_id: string; checklist_version_id: string; version: number
  previous_version_id: string | null; derived_from_version_id: string | null
}
export interface ChecklistDraft {
  checklist_id: string; revision: number; base_version_id: string | null
  derived_from_version_id: string | null; content: ChecklistContent
}
export interface ChecklistSummary {
  checklist_id: string; title: string; channels: Channel[]; draft_revision: number
  latest_version_id: string | null; version_count: number; has_unpublished_changes: boolean
}
export type Answers = Record<string, boolean | number | null>
export interface CalculatedResult {
  criteria: {
    criterion_id: string; raw_value: boolean | number | null
    applicability: 'applicable' | 'not_applicable'; weight: number
    normalized_value: string | null; earned_points: string | null
    effective_weight: string | null; weighted_contribution: string | null
  }[]
  sections: { section_id: string; applicable_weight: string; weighted_contribution: string | null; local_percent: string | null }[]
  applicable_weight: string; earned_points: string; total: string | null; gross_error: boolean
}
