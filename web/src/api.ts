export interface AdminUser { id: string; username: string; expires_at: number }
export interface AuthResponse { user: AdminUser; csrf_token: string }
export interface Preferences { site_name: string; timezone: string; revision: number; updated_at: number | null }
export interface ServerStatus {
  version: string; phase: string; started_at: number; server_time: number; uptime_seconds: number
  database: { engine: string; schema_version: number; journal_mode: string }
  active_admin_sessions: number; event_connections: number
  credentials: { encrypted: boolean; key_source: string }; static_ready: boolean
  capabilities: { administration: boolean; model_connections: boolean; qq: boolean; roleplay: boolean }
  models: { connections: number; enabled: number; assigned: number }
  qq: { accounts: number; connected: number; instances: number; received: number; images: { counts: Record<string, number>; last_error: string | null } }
}
export interface SystemInfo {
  version: string; python: string; platform: string; data_dir: string; public_origin: string
  allowed_origins: string[]; cookie_secure: boolean; session_seconds: number
  session_idle_seconds: number; database_schema: number
}
export interface BrowserSession {
  id: string; created_at: number; last_seen_at: number; expires_at: number
  peer: string; user_agent: string; current: boolean
}
export interface AuditEvent { id: number; created_at: number; action: string; details: Record<string, unknown> }
export interface AuditPage { items: AuditEvent[]; next_before: number | null }

let csrf = ''
let sessionEpoch = 0
export function setCsrf(value: string) { csrf = value; sessionEpoch++ }

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}, timeoutMs = 15000): Promise<T> {
  const epoch = sessionEpoch
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (typeof options.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && !['GET', 'HEAD'].includes(options.method)) headers.set('X-RP-CSRF', csrf)
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'same-origin', signal: controller.signal })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      let message = typeof body.detail === 'string' ? body.detail : `请求失败（${response.status}）`
      if (Array.isArray(body.detail)) message = body.detail.map((item: { message?: string }) => item.message ?? '输入无效').join('；')
      // 退出后重新登录时，旧请求的迟到401不能注销新会话。
      if (response.status === 401 && path !== '/auth/login' && epoch === sessionEpoch) window.dispatchEvent(new Event('rp-auth-expired'))
      throw new ApiError(response.status, message, body.request_id ?? response.headers.get('X-Request-ID') ?? undefined)
    }
    if (response.status === 204) return undefined as T
    return await response.json() as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new Error('请求超时。操作结果可能尚未确认，请刷新查看后再操作。')
    throw error
  } finally { window.clearTimeout(timeout) }
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError && error.requestId && error.status >= 500) return `${error.message}（${error.requestId}）`
  return error instanceof Error ? error.message : '操作未完成，请稍后重试。'
}

export function formatTime(value: number | null | undefined, timezone = 'Asia/Shanghai'): string {
  if (value == null) return '尚无记录'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value * 1000))
}
