export interface McpTool { name: string; description: string | null; inputSchema: Record<string, unknown>; outputSchema: Record<string, unknown> | null; annotations: Record<string, unknown> | null; eligible: boolean; reason: string | null }
export interface McpServer { id: string; name: string; url: string; enabled: boolean; auth_type: 'none' | 'bearer' | 'api_key'; connect_timeout: number; call_timeout: number; revision: number; key_configured: boolean; catalog_revision: number; tools: McpTool[]; discovered_at: number | null; last_error: string | null }
export interface McpSync { id: string; server_id: string; status: string; created_at: number; error: string | null; tool_count: number | null }
export interface McpCatalog { servers: McpServer[]; syncs: McpSync[]; active_runs: number }
export interface McpGrant { server_id: string; name: string; catalog_revision: number }
export interface McpPermissions { revision: number; tools: McpGrant[] }
export interface McpCall { id: number; instance_id: string; server_id: string; tool_name: string; call_id: string; run_id: number; status: string; result_json: string | null; error: string | null; created_at: number }
export const mcpActive = (status: string) => ['queued', 'running', 'connecting'].includes(status)
const labels: Record<string, string> = { queued: '等待中', running: '进行中', connecting: '连接中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断', unknown: '结果未知' }
export const mcpStatus = (status: string) => labels[status] || status
