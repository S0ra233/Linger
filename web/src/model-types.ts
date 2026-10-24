export type Protocol = 'chat_completions' | 'responses' | 'anthropic_messages' | 'gemini' | 'embedding_openai' | 'embedding_ollama' | 'embedding_gemini'
export interface ReasoningSettings {
  thinking_mode?: 'enabled' | 'disabled' | 'adaptive' | 'between_tools' | null; thinking_budget?: number | null; effort_delivery?: 'top_level' | 'message'; baseline_effort?: string | null
  adapter: string; effort_map: Record<string, string | number>; tool_efforts: string[] | null; default_thinking: boolean | null
  temperature_efforts: string[] | null; image_efforts: string[] | null
  min_output_tokens: number | null; max_output_tokens: number | null; max_thinking_tokens: number | null
}
export interface ReasoningCatalog { efforts: string[]; adapters: { id: string; label: string; protocols: string[]; native_levels: string[]; budget: boolean }[] }
export interface ToolPolicySettings {
  plan_output?: 'system' | 'tool' | 'json'
  normal_mode: 'system' | 'auto' | 'required'; repair_mode: 'system' | 'prompt' | 'named' | 'allowed'; force_with_thinking: boolean
  schema_mode: 'compatible' | 'strict'; parallel_mode: 'default' | 'serial'
}
export interface ModelEntry {
  profile: import('./preset-types').ProfileBinding | null
  id: string | null; model: string; label: string; image_input: boolean | null; tool_input: boolean | null
  tool_policy: ToolPolicySettings | null
  context_window: number | null; max_output_tokens: number; temperature: number | null
  reasoning: ReasoningSettings | null; reasoning_effort: string | null; embedding_dimensions: number | null
}
export interface ProtocolOptions {
  output_limit_field: 'max_tokens' | 'max_completion_tokens' | null
  stream_usage: boolean; include_encrypted_reasoning: boolean; cache_control: boolean; gemini_explicit_cache_url?: string | null; gemini_explicit_cache_confirmed?: boolean
}
export interface ConnectionSpec {
  name: string; protocol: Protocol; provider_kind: string; endpoint_url: string; endpoint_mode: 'base' | 'full'; enabled: boolean
  timeout_seconds: number; connect_timeout_seconds: number; max_attempts: number
  options: ProtocolOptions; models: ModelEntry[]
}
export interface ConnectionDraft extends ConnectionSpec { revision: number; key_action: 'keep' | 'replace' | 'clear'; api_key: string | null }
export interface SavedConnection extends ConnectionSpec {
  endpoint_base_url: string | null; resolved_endpoint_url: string
  id: string; revision: number; archived: boolean; key_configured: boolean; updated_at: number; latest_check: ModelCheck | null
}
export interface ModelBinding { connection_id: string; model_id: string }
export type Purpose = 'narrator' | 'summary' | 'vision' | 'embedding'
export type Assignments = Record<Purpose, ModelBinding | null> & { revision: number }
export interface ModelCatalog { connections: SavedConnection[]; assignments: Assignments; protocols: Record<string, string> }
export interface ModelCheck {
  id: string; connection_id: string; model_id: string; connection_revision: number; status: string
  created_at: number; finished_at: number | null; error: string | null
  result: { tool_policy?: Record<string, unknown>; check_kind?: 'connection' | 'plan'; text?: string; dimensions?: number; completion_status?: string; finish_reason?: string; tool_calls?: number; usage: Record<string, number | null> | null; max_output_tokens?: number; format_check?: { scope: 'minimal_plan'; output?: 'json' | 'tool'; passed: boolean; issues: { path: string; message: string }[]; returned_tools: { name: string; call_id: string; arguments: string; arguments_truncated: boolean }[] } } | null
}
export const purposes: { id: Purpose; label: string }[] = [
  { id: 'narrator', label: '主叙事' }, { id: 'summary', label: '摘要' }, { id: 'vision', label: '视觉' }, { id: 'embedding', label: 'Embedding' },
]
export function newModel(): ModelEntry {
  return { id: null, model: '', label: '', image_input: null, tool_input: null, tool_policy: null, context_window: null, profile: null,
    max_output_tokens: 4096, temperature: null, reasoning: null, reasoning_effort: null, embedding_dimensions: null }
}
export function newOptions(): ProtocolOptions { return { output_limit_field: null, stream_usage: false, include_encrypted_reasoning: false, cache_control: false } }
export const checkLabels: Record<string, string> = {
  queued: '等待中', running: '检查中', succeeded: '已完成', failed: '失败', incomplete: '响应未完成',
  cancelled: '已取消', unknown: '结果未知', cancelling: '取消中',
}
