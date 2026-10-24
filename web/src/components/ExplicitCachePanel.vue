<script setup lang="ts">
import { computed, onMounted, onScopeDispose, reactive, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { ModelCatalog } from '../model-types'
import type { RoleInstance } from '../roleplay-types'
import EditDialog from './EditDialog.vue'
const props = defineProps<{ catalog: ModelCatalog; timezone: string }>()
interface Cache { id: string; instance_id: string; connection_id: string; model_id: string; model: string; status: string; name: string | null; expires_at: number | null; token_count: number | null; revision: number; error: string | null }
interface Baseline { instance_id: string; connection_id: string; model_id: string; bytes: number }
interface Data { items: Cache[]; baselines: Baseline[]; operations: { id: number; cache_id: string; operation: string; status: string; created_at: number; error: string | null }[] }
const data = ref<Data | null>(null), lines = ref<RoleInstance[]>([]), error = ref(''), editorError = ref(''), busy = ref(false), open = ref(false), chosen = ref(''), mode = ref<'create' | 'extend'>('create'), target = ref<Cache | null>(null)
const dialog = ref<InstanceType<typeof EditDialog> | null>(null), ttl = reactive({ hours: 0, minutes: 10, seconds: 0 })
const total = computed(() => ttl.hours * 3600 + ttl.minutes * 60 + ttl.seconds)
const names: Record<string, string> = { active: '可使用', expired: '已到期', unknown: '结果未知', creating: '建立中', extending: '延长中', refreshing: '查询中', deleting: '删除中', deleted: '已删除', disabled: '本地停用' }
const actions: Record<string, string> = { create: '建立', refresh: '查询', extend: '延长 TTL', delete: '删除' }
const key = (row: Baseline) => `${row.instance_id}:${row.connection_id}:${row.model_id}`
const lineName = (id: string) => { const row = lines.value.find(item => item.id === id); return row ? `${row.display_name || row.nickname || row.peer_id} · ${row.character_name}` : id }
const connectionName = (id: string) => props.catalog.connections.find(item => item.id === id)?.name ?? id
let version = 0, disposed = false
async function load() {
  const mine = ++version
  try { const [value, instances] = await Promise.all([api<Data>('/models/explicit-caches'), api<RoleInstance[]>('/roleplay/instances')]); if (mine === version) { data.value = value; lines.value = instances; error.value = '' } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
}
function edit(row?: Cache) { mode.value = row ? 'extend' : 'create'; target.value = row ?? null; chosen.value = data.value?.baselines[0] ? key(data.value.baselines[0]) : ''; Object.assign(ttl, { hours: 0, minutes: 10, seconds: 0 }); editorError.value = ''; open.value = true }
async function save() {
  if (busy.value) return
  busy.value = true; editorError.value = ''
  try {
    if (mode.value === 'create') {
      const [instance_id, connection_id, model_id] = chosen.value.split(':')
      await api('/models/explicit-caches', { method: 'POST', body: JSON.stringify({ instance_id, connection_id, model_id, ttl_seconds: total.value }) }, 50000)
    } else if (target.value) await api(`/models/explicit-caches/${target.value.id}/operation`, { method: 'POST', body: JSON.stringify({ revision: target.value.revision, action: 'extend', ttl_seconds: total.value }) }, 50000)
    if (!disposed) { open.value = false; await load() }
  } catch (cause) { if (!disposed) editorError.value = errorText(cause) }
  finally { busy.value = false }
}
async function operate(row: Cache, action: string) {
  if (busy.value) return
  busy.value = true
  try {
    if (action !== 'refresh') { try { await ElMessageBox.confirm(action === 'delete' ? '删除远端缓存对象。后续请求恢复发送完整前缀。' : '仅停止本地使用；远端对象继续到原 TTL 到期，存储费用不会因此立即停止。', action === 'delete' ? '删除缓存' : '本地停用', { confirmButtonText: '确认', cancelButtonText: '取消' }) } catch { return } }
    if (disposed) return
    await api(`/models/explicit-caches/${row.id}/operation`, { method: 'POST', body: JSON.stringify({ revision: row.revision, action }) }, 50000)
    if (!disposed) await load()
  } catch (cause) { if (!disposed) error.value = errorText(cause) }
  finally { busy.value = false }
}
const available = (row: Cache) => !['creating','extending','refreshing','deleting','deleted'].includes(row.status)
onMounted(load); onScopeDispose(() => { version++; disposed = true })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span>Gemini 固定前缀缓存</span><div><el-button :disabled="busy" @click="load">刷新</el-button><el-button type="primary" :disabled="busy || !data?.baselines.length" @click="edit()">建立缓存</el-button></div></div>
  <el-empty v-if="!data?.items.length" description="暂无显式缓存对象" />
  <el-alert v-if="data && !data.baselines.length" title="先在连接高级设置确认缓存地址，并完成一轮有效 Gemini 业务。" type="info" :closable="false" class="form-alert" />
  <div v-if="data?.items.length" class="table-scroll panel"><el-table :data="data.items"><el-table-column label="生活线／模型" min-width="180"><template #default="{ row }"><strong>{{ lineName(row.instance_id) }}</strong><p>{{ connectionName(row.connection_id) }} · {{ row.model }}</p></template></el-table-column><el-table-column label="状态" width="120"><template #default="{ row }"><el-tag effect="plain" :type="row.status === 'active' ? 'success' : 'info'">{{ names[row.status] || row.status }}</el-tag></template></el-table-column><el-table-column label="到期时间" min-width="180"><template #default="{ row }">{{ formatTime(row.expires_at, timezone) }}</template></el-table-column><el-table-column label="缓存 tokens" width="120"><template #default="{ row }">{{ row.token_count ?? '未知' }}</template></el-table-column><el-table-column label="操作" min-width="230"><template #default="{ row }"><el-button link :disabled="busy || !available(row)" @click="operate(row, 'refresh')">查询</el-button><el-button link :disabled="busy || row.status !== 'active'" @click="edit(row)">延长</el-button><el-button link :disabled="busy || !available(row) || !row.name" @click="operate(row, 'delete')">删除</el-button><el-button link :disabled="busy || !available(row)" @click="operate(row, 'disable')">停用</el-button></template></el-table-column><el-table-column type="expand"><template #default="{ row }"><p class="message-text">{{ row.name || '远端编号未知' }}</p><p>{{ row.error || '固定前缀完全一致时使用；不承诺请求必然命中。' }}</p></template></el-table-column></el-table></div>
  <el-collapse v-if="data?.operations.length" class="quiet-collapse"><el-collapse-item title="资源操作记录" name="operations"><article v-for="row in data.operations" :key="row.id" class="check-record"><div class="section-toolbar"><span>{{ actions[row.operation] || row.operation }} · {{ names[row.status] || row.status }}</span><time>{{ formatTime(row.created_at, timezone) }}</time></div><p v-if="row.error">{{ row.error }}</p></article></el-collapse-item></el-collapse>
  <EditDialog ref="dialog" v-model="open" :draft="[ttl,chosen]" :busy="busy" :title="mode === 'create' ? '建立显式缓存' : '延长缓存 TTL'" width="650px"><el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" /><el-form label-position="top" @submit.prevent="save"><el-form-item v-if="mode === 'create'" label="最近有效业务前缀"><el-select v-model="chosen"><el-option v-for="row in data?.baselines" :key="key(row)" :value="key(row)" :label="`${lineName(row.instance_id)} / ${connectionName(row.connection_id)} · ${Math.ceil(row.bytes/1024)} KiB`" /></el-select></el-form-item><el-form-item label="缓存保留时长"><div class="inline-actions"><el-input-number v-model="ttl.hours" :min="0" :max="168" aria-label="小时" /><span>时</span><el-input-number v-model="ttl.minutes" :min="0" :max="59" aria-label="分钟" /><span>分</span><el-input-number v-model="ttl.seconds" :min="0" :max="59" aria-label="秒" /><span>秒</span></div></el-form-item><p>创建与保留缓存按供应商规则计费；金额尚未取得。</p><div class="form-actions"><el-button @click="dialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="total < 60 || total > 604800 || (mode === 'create' && !chosen)">{{ mode === 'create' ? '建立缓存' : '延长 TTL' }}</el-button></div></el-form></EditDialog>
</template>
