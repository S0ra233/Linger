export interface Character {
  id: string; name: string; description: string; personality: string; scenario: string
  dialogue_examples: string; first_message: string; style: string; revision: number; updated_at: number
  nickname: string; system_prompt: string; post_history_instructions: string; alternate_greetings: string[]
  worldbooks: { id: string; revision: number }[]
  knowledge_libraries: string[]
}
export interface QQConnection {
  connected: boolean; connected_at?: number; disconnected_at?: number; last_event_at?: number
  last_error?: string | null; received?: number; duplicates?: number
  handshake_error?: string; handshake_error_code?: string; handshake_failed_at?: number
}
export interface QQAccount {
  id: string; bot_id: string; name: string; enabled: boolean; revision: number
  token_configured: boolean; websocket_url: string; connection: QQConnection
}
export interface QQAccountsResponse {
  items: QQAccount[]; images: { counts: Record<string, number>; last_error: string | null }
}
export interface Contact {
  account_id: string; peer_id: string; nickname: string; display_name: string
  instance_id: string | null; enabled: number; revision: number; last_seen_at: number
  character_id: string | null; character_name: string | null; character_revision: number | null
  input_version: number | null; message_count: number
}
export interface RoleInstance {
  id: string; account_id: string; account_name: string; peer_id: string; nickname: string; display_name: string
  character_id: string; character_name: string; character_revision: number; latest_character_revision: number
  receiving: number; input_version: number; message_count: number; created_at: number
}
export type MessagePart = { type: 'text'; text: string } | { type: 'reply'; message_id: string }
  | { type: 'unsupported'; kind: string }
  | { type: 'image'; image_id: string; status: 'pending' | 'fetching' | 'ready' | 'failed'; url?: string; error: string | null; market_available?: boolean }
export interface InputMessage {
  id: number; instance_id: string; account_id: string; message_id: string; event_at: number
  received_at: number; parts: MessagePart[]
}
export interface InputMessagesPage { items: InputMessage[]; next_before: number | null }
