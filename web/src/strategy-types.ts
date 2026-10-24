import type { ContextSettings } from './material-types'
export type StrategyRules = Pick<ContextSettings,
  'strategy' | 'history_trigger_turns' | 'history_trigger_tokens' | 'history_target_tokens' | 'retain_turns' |
  'trim_on_material_change' | 'state_injection' | 'state_refresh_turns' | 'context_window' | 'context_bytes' | 'output_reserve' | 'safety_margin' | 'trigger_ratio' |
  'worldbook_budget_tokens' | 'memory_budget_tokens' | 'knowledge_budget_tokens' | 'retrieval_mode' | 'retrieval_top_k' |
  'retrieval_budget_tokens' | 'retrieval_target_tokens' | 'allow_information_tools' | 'retain_retrieval_snapshot' |
  'summary_target_tokens' | 'summary_max_tokens' | 'summary_timeout' | 'summary_max_batches' | 'pending_input_limit'>
export interface ContextStrategy { id: string; name: string; revision: number; config: StrategyRules; bound_count?: number; updated_at: number }
export interface StrategyCatalog { items: ContextStrategy[]; defaults: { cost: StrategyRules; continuity: StrategyRules } }
export interface StrategyBinding { instance_id: string; strategy_id: string | null; strategy_revision: number | null; revision: number; overrides: Partial<StrategyRules>; strategy: ContextStrategy | null; latest_revision?: number }
export interface BoundConversation { id: string; display_name: string; nickname: string; peer_id: string; account_name: string; character_name: string; strategy_id: string | null; strategy_revision: number | null; strategy_name: string | null; latest_revision: number | null; binding_revision: number; overridden: boolean }
export const conversationLabel = (item: BoundConversation) => `${item.display_name || item.nickname || item.peer_id} · ${item.character_name} · ${item.account_name}`
export const strategyModes: Record<string, string> = { cost: '成本优先', continuity: '对话连续性优先', legacy: '旧版配置' }
