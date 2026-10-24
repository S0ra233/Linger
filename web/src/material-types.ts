export interface BookSummary { id: string; name: string; revision: number; entry_count: number; updated_at: number }
export interface WorldEntry {
  id: string; name: string; content: string; keys: string[]; secondary_keys: string[]
  selective_logic: 'none' | 'and_any' | 'and_all' | 'not_any' | 'not_all'
  constant: boolean; enabled: boolean; case_sensitive: boolean; insertion_order: number; priority: number
  position: 'before_char' | 'after_char'
  run_types: ('input' | 'life')[]
  sticky: number; cooldown: number; delay: number
}
export interface Worldbook { id?: string; name: string; description: string; scan_depth: number; token_budget: number; entries: WorldEntry[]; revision: number; import_id?: string | null }
export interface MaterialImport { id: string; kind: 'character' | 'worldbook'; filename: string; format: string; source_hash: string; raw: Record<string, unknown>; normalized: Record<string, unknown>; warnings: string[]; committed_id: string | null }
export interface ContextSettings {
  strategy: 'legacy' | 'cost' | 'continuity'; history_trigger_turns: number
  history_trigger_tokens: number | null; history_target_tokens: number | null
  retrieval_enabled: boolean; retrieval_refresh_turns: number; retrieval_budget_tokens: number; retrieval_target_tokens: number
  allow_information_tools: boolean; retain_retrieval_snapshot: boolean
  trim_on_material_change: boolean
  state_injection: 'off' | 'snapshot'; state_refresh_turns: number
  context_bytes: number; prompt_blocks: import('./preset-types').PromptBlock[]
  context_window: number | null; output_reserve: number; safety_margin: number; trigger_ratio: number
  retain_turns: number; worldbook_budget_tokens: number; auto_summary: boolean; summary_target_tokens: number
  retrieval_mode: 'keyword' | 'semantic' | 'hybrid'
  memory_budget_tokens: number; knowledge_budget_tokens: number; retrieval_top_k: number
  summary_max_tokens: number; summary_timeout: number; summary_max_batches: number; pending_input_limit: number; role_prompt: string; post_history_prompt: string
  summary_instructions: string; tool_submission_hint: string; revision: number
}
export interface PlanContract {
  output?: 'tool' | 'json'
  fields: string[]; omitted_fields: string[]; enabled_extensions: string[]; schema_hash: string; tool_names: string[]
}
export interface RequestBudget {
  input_bytes: number; byte_limit: number; estimated_input_tokens: number; window: number | null
  budget_input_tokens?: number
  budget_calibration?: { method: 'coarse' | 'anchored' | 'continuation'; samples: number; reason?: string; anchor_step_id?: number; anchor_input_tokens?: number; shared_messages?: number; margin_tokens?: number; suffix_tokens?: number }
  plan_contract?: PlanContract | null
  output_reserve: number; safety_margin: number; available_input_tokens: number | null; over_budget: boolean
}
export interface ContextReport extends RequestBudget {
  policy?: Omit<ContextSettings, 'revision'>
  layers?: { id: string; label: string; tokens: number }[]
  sequence?: { layer: string; label: string; tokens: number }[]
  history_turns?: number; retained_after_trim?: number; would_trim?: boolean; trim_reason?: string | null
  estimated_reusable_prefix_tokens?: number; first_changed_layer?: string | null
  history_rule_conflict?: boolean; material_changed?: boolean; retrieval_refreshed?: boolean
  after_trim_estimated_input_tokens?: number
  native_breakpoints?: string
  retrieval_query?: string; retrieval_basis?: Record<string, unknown>
  scene?: import('./information-types').Scene; history_incomplete?: boolean; pending_overflow?: boolean; pending_total?: number; summary_batches?: number
  run_type?: 'input' | 'life'
  prompt_blocks?: { id: string; name: string; role: string; position: string; run_types: string[]; selected: boolean; reason: string }[]
  information?: Record<string, unknown>
  fixed_over_budget: boolean
  would_summarize: boolean; can_summarize: boolean; pending_inputs: number; historical_messages: number
  summary_id: number | null; prefix_hash: string; worldbooks: { before: string[]; after: string[]; trace: WorldbookTrace[]; estimated_tokens: number }
}
export interface PreviewMessage {
  role: 'system' | 'user' | 'assistant'
  content: ({ type: 'text'; text: string } | { type: 'image'; attachment_id: string })[]
  context_layer: string | null
  [key: string]: unknown
}
export interface ContextPreview { report: ContextReport; messages: PreviewMessage[] }
export interface WorldbookTrace {
  worldbook_id: string; worldbook_name?: string; revision: number; entry_id: string; name: string
  constant: boolean; selected: boolean; estimated_tokens: number; reason: string
  primary_hits?: string[]; secondary_hits?: string[]; scan_depth?: number; run_types?: string[]
}
export interface SummaryRecord { id: number; run_id: number; status: string; text: string | null; source_json: string; usage_json: string | null; model_json: string; error: string | null; created_at: number }
