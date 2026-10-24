<script setup lang="ts">
import { computed, onMounted, onScopeDispose, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import EditDialog from '../components/EditDialog.vue'
import DataDetails from '../components/DataDetails.vue'
import type { McpCatalog, McpServer, McpTool, McpCall } from '../mcp-types'
import { mcpActive, mcpStatus } from '../mcp-types'

const props = defineProps<{ refreshKey: number; timezone: string }>()
const status = ref<McpCatalog | null>(null), calls = ref<McpCall[]>([]), loading = ref(false), error = ref(''), busy = ref(''), tab = ref('servers')
const dialog = ref<InstanceType<typeof EditDialog> | null>(null), visible = ref(false), saving = ref(false), formError = ref(''), identifier = ref('')
const initial = () => ({ name: '', url: '', enabled: false, auth_type: 'bearer' as McpServer['auth_type'], key_action: 'keep' as 'keep' | 'replace' | 'clear', token: '', connect_timeout: 20, call_timeout: 45, revision: 0 })
const form = reactive(initial())
const inspected = ref<McpTool | null>(null), detailVisible = ref(false), selected = ref('')
const tools = computed(() => status.value?.servers.find(server => server.id === selected.value)?.tools ?? [])
let version = 0
async function load() {
  const mine = ++version; loading.value = true
  try {
    const [value, history] = await Promise.all([api<McpCatalog>('/mcp'), api<McpCall[]>('/mcp/calls')])
    if (mine !== version) return
    status.value = value; calls.value = history; error.value = ''
    if (!value.servers.some(server => server.id === selected.value)) selected.value = value.servers[0]?.id ?? ''
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function edit(server?: McpServer) {
  identifier.value = server?.id ?? ''
  Object.assign(form, initial(), server ? { name: server.name, url: server.url, enabled: server.enabled, auth_type: server.auth_type, connect_timeout: server.connect_timeout, call_timeout: server.call_timeout, revision: server.revision } : {})
  formError.value = ''; visible.value = true
}
async function save() {
  if (saving.value) return
  saving.value = true; formError.value = ''
  try {
    await api(identifier.value ? `/mcp/servers/${identifier.value}` : '/mcp/servers', { method: identifier.value ? 'PUT' : 'POST', body: JSON.stringify({ ...form, token: form.key_action === 'replace' ? form.token : null }) })
    visible.value = false; form.token = ''; ElMessage.success('连接已保存'); await load()
  } catch (cause) { formError.value = errorText(cause) }
  finally { saving.value = false }
}
async function sync(server: McpServer) {
  if (busy.value) return
  busy.value = server.id
  try { await api(`/mcp/servers/${server.id}/sync`, { method: 'POST', body: JSON.stringify({ revision: server.revision }) }); ElMessage.success('目录同步已开始'); await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function cancel(id: string) {
  if (busy.value) return
  busy.value = id
  try { await api(`/mcp/syncs/${id}/cancel`, { method: 'POST' }); await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
watch(() => props.refreshKey, load); onMounted(load); onScopeDispose(() => { version++; form.token = '' })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span class="muted">连接与工具目录</span><div><el-button :loading="loading" @click="load">刷新</el-button><el-button type="primary" @click="edit()">添加连接</el-button></div></div>
  <el-alert title="仅连接你信任的服务。只读标记由远端声明，不能保证远端没有副作用。" type="info" :closable="false" class="form-alert" />
  <el-tabs v-model="tab" class="management-tabs"><el-tab-pane label="连接" name="servers"><section class="panel" v-loading="loading && !status"><el-empty v-if="status && !status.servers.length && !loading && !error" description="尚无 MCP 连接"><el-button @click="edit()">添加连接</el-button></el-empty><article v-for="server in status?.servers ?? []" :key="server.id" class="check-record"><div class="section-toolbar"><strong>{{ server.name }}</strong><div><el-tag effect="plain" :type="server.enabled ? 'success' : 'info'">{{ server.enabled ? '已启用' : '未启用' }}</el-tag><el-button link @click="edit(server)">编辑</el-button><el-button :disabled="!server.enabled || !!busy || !!status?.syncs.some(job => job.server_id === server.id && mcpActive(job.status))" :loading="busy === server.id" @click="sync(server)">同步目录</el-button></div></div><p class="message-text">{{ server.url }}</p><div class="check-metadata"><span>{{ server.tools.filter(tool => tool.eligible).length }} 项可授权 / {{ server.tools.length }} 项工具</span><span>{{ server.discovered_at ? formatTime(server.discovered_at, timezone) : '尚未同步' }}</span><span>{{ server.key_configured ? '已保存密钥' : '未配置密钥' }}</span></div><p v-if="server.last_error" class="check-error">{{ server.last_error }}</p></article></section></el-tab-pane>
    <el-tab-pane label="工具目录" name="tools"><section class="panel table-scroll"><el-select v-model="selected" placeholder="选择连接"><el-option v-for="server in status?.servers ?? []" :key="server.id" :value="server.id" :label="server.name" /></el-select><el-table :data="tools" empty-text="请先启用连接并同步目录"><el-table-column prop="name" label="工具" min-width="150" /><el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip /><el-table-column label="可用范围" min-width="170"><template #default="{ row }">{{ row.eligible ? '可按生活线授权' : row.reason }}</template></el-table-column><el-table-column label="结构" width="80"><template #default="{ row }"><el-button link @click="inspected = row; detailVisible = true">查看</el-button></template></el-table-column></el-table><div class="form-actions"><a href="#contacts">前往联系人授权</a></div></section></el-tab-pane>
    <el-tab-pane label="同步记录" name="syncs"><section class="panel"><el-empty v-if="!status?.syncs.length" description="暂无同步记录" /><article v-for="job in status?.syncs ?? []" :key="job.id" class="check-record"><div class="section-toolbar"><strong>{{ status?.servers.find(server => server.id === job.server_id)?.name || job.server_id }}</strong><div><el-tag effect="plain">{{ mcpStatus(job.status) }}</el-tag><el-button v-if="mcpActive(job.status)" :disabled="!!busy" @click="cancel(job.id)">取消</el-button></div></div><p class="check-metadata">{{ formatTime(job.created_at, timezone) }}{{ job.tool_count == null ? '' : ` · ${job.tool_count} 项工具` }}</p><p v-if="job.error" class="check-error">{{ job.error }}</p></article></section></el-tab-pane>
    <el-tab-pane label="调用记录" name="calls"><section class="panel table-scroll"><el-table :data="calls" empty-text="暂无外部调用"><el-table-column prop="tool_name" label="工具" min-width="150" /><el-table-column label="运行" width="85"><template #default="{ row }">#{{ row.run_id }}</template></el-table-column><el-table-column label="状态" min-width="100"><template #default="{ row }">{{ mcpStatus(row.status) }}</template></el-table-column><el-table-column label="时间" min-width="170"><template #default="{ row }">{{ formatTime(row.created_at, timezone) }}</template></el-table-column><el-table-column prop="error" label="诊断" min-width="200" show-overflow-tooltip /></el-table></section></el-tab-pane>
  </el-tabs>
  <EditDialog ref="dialog" v-model="visible" :draft="form" :busy="saving" :title="identifier ? '编辑 MCP 连接' : '添加 MCP 连接'" width="600px" @closed="form.token = ''"><el-alert v-if="formError" :title="formError" type="error" :closable="false" class="form-alert" /><el-alert v-if="identifier" title="配置更改后需重新同步目录，并为生活线授权。" type="info" :closable="false" class="form-alert" /><el-form label-position="top" :disabled="saving" @submit.prevent="save"><el-form-item label="名称"><el-input v-model="form.name" maxlength="80" /></el-form-item><el-form-item label="Streamable HTTP 地址"><el-input v-model="form.url" placeholder="https://example.com/mcp" maxlength="2000" /></el-form-item><el-form-item label="认证"><el-select v-model="form.auth_type"><el-option value="none" label="无需认证" /><el-option value="bearer" label="Bearer Token" /><el-option value="api_key" label="X-API-Key" /></el-select></el-form-item><el-form-item label="密钥"><el-radio-group v-model="form.key_action"><el-radio-button value="keep">保留</el-radio-button><el-radio-button value="replace">填写新密钥</el-radio-button><el-radio-button value="clear">清除</el-radio-button></el-radio-group><el-input v-if="form.key_action === 'replace'" v-model="form.token" type="password" autocomplete="new-password" show-password maxlength="4096" class="section-gap" /></el-form-item><el-collapse class="quiet-collapse"><el-collapse-item title="超时设置" name="timeouts"><div class="form-two-columns"><el-form-item label="连接与目录（秒）"><el-input-number v-model="form.connect_timeout" :min="3" :max="60" /></el-form-item><el-form-item label="工具调用（秒）"><el-input-number v-model="form.call_timeout" :min="5" :max="180" /></el-form-item></div></el-collapse-item></el-collapse><div class="form-actions"><el-switch v-model="form.enabled" active-text="启用连接" /><span class="action-spacer"></span><el-button @click="dialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!form.name.trim() || !form.url.trim() || form.key_action === 'replace' && !form.token">保存</el-button></div></el-form></EditDialog>
  <el-drawer v-model="detailVisible" title="工具结构" size="min(760px, 100vw)" class="detail-drawer" append-to-body><DataDetails v-if="inspected" :value="inspected" /></el-drawer>
</template>
