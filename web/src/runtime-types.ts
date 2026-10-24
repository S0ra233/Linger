import type { RoleInstance } from './roleplay-types'

export interface RuntimeSettings {
  enabled: boolean; concurrency: number; debounce_seconds: number; history_messages: number; context_bytes: number
  max_steps: number; max_tool_calls: number; run_timeout: number; receipt_timeout: number; delivery_grace: number
  schedule_hours: number; hide_html_comments: boolean; revision: number
}
export interface RuntimeInstance extends RoleInstance {
  paused: number; pending_inputs: number; next_delivery: number | null; handled_input_id: number; attempted_version: number
}
export interface RuntimeOverview {
  settings: RuntimeSettings; summary: { generating: number; sending: number; last_error: string | null }; instances: RuntimeInstance[]
}
export interface RpRun {
  id: number; instance_id: string; input_version: number; input_through: number; status: string
  error: string | null; created_at: number; finished_at: number | null; model_json: string | null
  settings_json?: string; decision_json?: string | null; context_json?: string | null; mcp_json?: string | null
  trigger_kind: 'input' | 'life' | 'manual'; life_json?: string | null
}
export interface RunsPage { items: RpRun[]; next_before: number | null }
export interface RpStep {
  id: number; step: number; status: string; request_json: string; response_json: string | null
  tools_json: string | null; created_at: number; finished_at: number | null
}
export interface RunVersions {
  character: { id: string; name: string; revision: number; adjustment_revision: number; worldbooks: { id: string; revision: number }[] }
  preset: { id: string | null; revision: number | null; local_overrides: boolean }
  strategy: { id: string | null; revision: number | null; binding_revision: number; rule_revision: number; mode: string; local_overrides: boolean }
}
export interface RunExplanation {
  versions: RunVersions | null; baseline_run_id: number | null; version_changes: { label: string; before: unknown; after: unknown }[]
  trace: { baseline_run_id: number | null; has_baseline: boolean; compatible: boolean | null; changed_layers: string[] } | null
  first_changed_layer: string | null; estimated_reusable_prefix_tokens: number | null
  transition: { stage: string; before_turns: number; after_turns: number; input_removed: number; sent_removed: number; retrieval_before?: number; retrieval_after?: number; retrieval_before_tokens?: number; retrieval_after_tokens?: number; reason: string | null; summary_batches: number } | null
  memory_outcomes?: { candidate: number; memory_id: string | null; proposal_id: string | null; status: 'queued' | 'existing_proposal' | 'queue_full' }[]
  planning: { reason: string | null; would_trim?: boolean; would_summarize?: boolean }; adopted: boolean
  operations: { id: number; created_at: number; action: string }[]; operations_limited: boolean; boundary: string
  budgets: { step: number; method: string; estimated_input_tokens: number; budget_input_tokens?: number; reason?: string }[]
  usage: { requests: number; reported_input_requests: number; reported_output_requests: number; input_tokens: number | null; output_tokens: number | null; paired_cache_requests: number; paired_input_tokens: number | null; cache_read_tokens: number | null; cache_ratio: number | null; repair_attempts: number | null; started_attempts: number | null; attempt_reports: number }
}
export type RunIssueCode = 'configuration' | 'context_invalid' | 'context_budget' | 'provider_request' | 'empty_response' | 'output_limit'
  | 'incomplete_response' | 'tool_identity' | 'tool_limit' | 'tool_json' | 'tool_arguments' | 'plan_json' | 'plan_validation' | 'tool_execution'
  | 'missing_plan' | 'tool_selection' | 'repair_exhausted' | 'step_limit' | 'commit_validation' | 'stale_run' | 'cancelled' | 'timeout' | 'internal'
export interface RunIssue {
  code: RunIssueCode; stage: 'setup' | 'context' | 'provider' | 'response' | 'tools' | 'cleanup' | 'commit' | 'runtime'
  message: string; step: number | null; step_id: number | null; tool: string | null; call_id: string | null
  repair_attempt: number | null; stage_elapsed_ms?: number | null; fields: { path: (string | number)[]; code: string; message: string }[]
}
export interface RunDiagnostics {
  failure: RunIssue | null; recorded: boolean
  long_plan_recovery?: { restored: number; plan_ids: string[] } | null
  steps: { step: number; step_id: number; status: string; repair_attempt: number | null; issues: RunIssue[] }[]
  deliveries: Record<'pending' | 'sending' | 'succeeded' | 'failed' | 'cancelled' | 'unknown' | 'expired', number>
}
export interface RunDetail { run: RpRun; steps: RpStep[]; explanation?: RunExplanation; diagnostics?: RunDiagnostics }
export interface Delivery {
  id: string; run_id: number; instance_id: string; position: number; text: string; status: string
  due_at: number; expires_at: number; started_at: number | null; finished_at: number | null
  message_id: string | null; error: string | null
  parts_json?: string
}
