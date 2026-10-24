export interface Library { id: string; name: string; enabled: number; revision: number; document_count: number; updated_at: number }
export interface KnowledgeDocument { id: string; library_id: string; title: string; text?: string; enabled: number; revision: number; updated_at: number; characters?: number; versions?: { revision: number; title: string; encoding: string; created_at: number }[] }
export interface Memory { id: string; instance_id: string; kind: 'profile' | 'temporary' | 'episode'; text: string; expires_at: number | null; status: 'active' | 'forgotten'; effective_status?: 'active' | 'forgotten' | 'expired' | 'source_invalid' | 'superseded'; revision: number; evidence_count?: number; created_at: number; updated_at: number; locked: boolean | number; protection_revision: number; confirmed_at: number | null; valid_from: number | null; superseded_by: string | null; superseded_at: number | null }
export interface MemoryProposal { id: string; revision: number; run_id: number; created_at: number; status: 'pending' | 'applied' | 'rejected'; effective_status: 'pending' | 'applied' | 'rejected' | 'stale'; stale_reason?: string | null; proposal_json: string; targets: Memory[] }
export interface MemoryProposalPage extends CursorPage<MemoryProposal> { pending_count: number; pending_limit: number }
export interface EvidenceRef { kind: 'input' | 'sent' | 'life' | 'world_event' | 'observation'; id: number; revision: number }
export interface EvidenceRecord extends EvidenceRef { at: number; text: string; meaning: string; deleted?: boolean; state_changes?: Record<string, string>; truncated?: boolean; validity?: { invalid: boolean | number; reason: string; revision: number; updated_at?: number } }
export interface ConversationWindow {
  anchor: EvidenceRef; anchor_turn: number; anchor_input: number; anchor_sent: number
  total: number; from_turn: number; to_turn: number; page: number; page_size: number
  items: { number: number; messages: { kind: 'input' | 'sent'; id: number; revision: number; at: number; text: string; is_anchor: boolean }[] }[]
  omitted: { number: number; reason: 'source_invalid' | 'message_limit' | 'token_budget' }[]
  token_budget: number; estimated_tokens: number; meaning: string
}
export interface EvidencePage { items: EvidenceRecord[]; next_before: number | null }
export interface Scene { status: 'active' | 'expired' | 'unknown' | 'source_invalid'; revision?: number; location?: string; activity?: string; participants?: string[]; unfinished?: string[]; expires_at?: number; sources?: EvidenceRef[] }
export interface MemoryDetail { memory: Memory; sources: EvidenceRecord[]; sources_limited?: boolean; replaces: { id: string; text: string; revision: number; superseded_at: number }[]; replacement: Memory | null; versions: { revision: number; run_id: number | null; config_json: string; created_at: number }[] }
export interface InformationResult { chunk_id: string; memory_id: string | null; document_id: string | null; revision: number; kind: string | null; title: string; library_id: string | null; text: string; start_line: number; end_line: number; score: number | null; conversation_anchors?: EvidenceRef[]; anchors_limited?: boolean }
export interface InformationResults { mode?: string; fallback?: string; space_id?: string; coverage?: { chunks: number; indexed: number; index_complete: boolean; scanned_vectors: number }[]; results: InformationResult[]; scan_complete: boolean; scored_chunks?: number; score_basis: string }
export interface CursorPage<T> { items: T[]; next_before: string | null }
