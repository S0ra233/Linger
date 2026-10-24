import type { ContextSettings } from './material-types'
export type RunType = 'input' | 'life' | 'summary'
export interface PromptBlock { id: string; name: string; role: 'system' | 'user' | 'assistant'; position: 'before_character' | 'after_character' | 'after_history'; content: string; enabled: boolean; run_types: RunType[] }
export interface PresetContent { context: Omit<ContextSettings, 'revision'>; max_output_tokens: number; temperature: number | null; reasoning_effort: string | null }
export interface ProfileBinding { preset_id: string | null; revision: number | null; overrides: { context?: Partial<PresetContent['context']>; max_output_tokens?: number; temperature?: number | null; reasoning_effort?: string | null } }
export interface PresetSummary { id: string; name: string; revision: number; updated_at: number }
export interface PresetSource { format: string; filename: string; sha256: string; raw: unknown; warnings: string[] }
export interface PresetDraft { name: string; config: PresetContent; revision: number; source: PresetSource | null }
export interface SavedPreset extends PresetDraft { id: string; updated_at: number }
export interface PresetCatalog { items: PresetSummary[]; defaults: PresetContent; legacy_defaults: PresetContent }
