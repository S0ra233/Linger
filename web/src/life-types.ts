export interface LifeSettings {
  enabled: boolean; followup_min_gap: number
  global_runs: number; instance_runs: number; max_unanswered: number; grace_seconds: number
  quiet_enabled: boolean; quiet_start: string; quiet_end: string; timezone: string; revision: number
}
export interface LifePolicy { enabled: boolean; revision: number }
export interface LifeOverview { settings: LifeSettings; runtime_enabled: boolean; active_runs: number; policies: { instance_id: string; enabled: number; revision: number }[] }
export interface LifeView { policy: LifePolicy; context: { unanswered_contacts: number; runs_24h: { global: number; instance: number }; background_contact_allowed: boolean; contact_blocked: string[] } }
export interface LifeIntent { id: string; instance_id: string; kind: 'life' | 'contact'; reason: string; due_at: number; expires_at: number; automatic: number; status: string; revision: number; source_run_id: number | null; run_id: number | null; error: string | null
  topic: string; sources: import('./information-types').EvidenceRef[]; commitment_source: import('./information-types').EvidenceRef | null
  abandon_if: string; deadline_at: number | null; max_evaluations: number; evaluations: number; legacy: boolean; resolution: string | null; outcome_reason: string
}
export interface LifeEvent { id: number; instance_id: string; run_id: number | null; kind: string; data_json: string; created_at: number }
export interface LifeTimeline { items: LifeEvent[]; next_before: number | null }
