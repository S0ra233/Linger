<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { ModelCatalog } from '../model-types'
import { protectPage } from '../ui-state'
interface Policy {
  instance_id: string; connection_id: string; model_id: string; enabled: boolean; mode: string; compatibility_confirmed: boolean
  interval_seconds: number; max_idle_seconds: number; daily_cap: number; failure_limit: number; output_tokens: number
  active_start: string; active_end: string; explicit_ttl_seconds?: number
}
interface State { revision: number; items: Policy[]; instances: { id: string; nickname: string; display_name: string; peer_id: string }[]
  targets: { instance_id: string; status: string; next_at: number | null; last_attempt: number | null; last_hit: number | null }[]
  calls: { id: number; status: string; created_at: number; error: string | null }[] }
defineProps<{ catalog: ModelCatalog; timezone: string }>()
const state = ref<State | null>(null), items = ref<Policy[]>([]), selected = ref(0), error = ref(''), busy = ref(false), baseline = ref('[]')
const current = computed(() => items.value[selected.value])
const statuses: Record<string, string> = { hit: '报告缓存读取', miss: '未命中', unverified: '缓存效果未知', failed: '失败', cancelled: '已取消', unknown: '中断后未知', running: '维护中', extended: 'TTL 已延长' }
const policyRevision = ref(0)
const dirty = computed(() => JSON.stringify(items.value) !== baseline.value)
protectPage(() => busy.value)
defineExpose({ allowLeave: async () => !busy.value })
let version = 0
async function load(force = false) {
  const request = ++version
  try { const data = await api<State>('/models/cache'); if (request !== version) return; state.value = data
    if (force || !dirty.value) { items.value = structuredClone(data.items); baseline.value = JSON.stringify(items.value); policyRevision.value = data.revision }
  } catch (cause) { if (request === version) error.value = errorText(cause) }
}
async function reloadRules() { if (!busy.value) await load(true) }
function add() {
  if (items.value.length >= 32) return
  items.value.push({ instance_id: '', connection_id: '', model_id: '', enabled: false, mode: 'unsupported', compatibility_confirmed: false,
    explicit_ttl_seconds: 600, interval_seconds: 270, max_idle_seconds: 3600, daily_cap: 24, failure_limit: 2, output_tokens: 16, active_start: '00:00', active_end: '00:00' }); selected.value = items.value.length - 1
}
function selectModel(value: string) { if (current.value) { const [connection, model] = value.split(':'); current.value.connection_id = connection ?? ''; current.value.model_id = model ?? ''; current.value.compatibility_confirmed = false; current.value.enabled = false; current.value.mode = 'unsupported' } }
function unit(divisor: number, modulus?: number) { return computed({ get: () => { const value = Math.floor((current.value?.interval_seconds ?? 0) / divisor); return modulus ? value % modulus : value },
  set: (value: number) => { if (!current.value) return; const old = Math.floor(current.value.interval_seconds / divisor); const part = modulus ? old % modulus : old; current.value.interval_seconds += ((value ?? 0) - part) * divisor } }) }
const hours = unit(3600), minutes = unit(60, 60), seconds = unit(1, 60)
async function save() {
  if (!state.value || busy.value) return
  busy.value = true; error.value = ''
  try { const data = await api<{ revision: number; items: Policy[] }>('/models/cache', { method: 'PUT', body: JSON.stringify({ revision: policyRevision.value, items: items.value }) });
    state.value.revision = data.revision; policyRevision.value = data.revision; items.value = data.items; baseline.value = JSON.stringify(items.value); await load()
  } catch (cause) { error.value = errorText(cause) } finally { busy.value = false }
}
onMounted(load); onUnmounted(() => { version++ })
</script>
<template>
  <div class="section-toolbar"><el-button :disabled="busy" @click="add">添加保活规则</el-button><div><el-button :disabled="busy" @click="load()">刷新状态</el-button><el-button :disabled="busy" @click="reloadRules">重载规则</el-button><el-button type="primary" :disabled="!dirty" :loading="busy" @click="save">保存规则</el-button></div></div>
  <el-alert v-if="dirty && state && state.revision !== policyRevision" title="规则已有其他修改；当前草稿保留，保存会核对原版本。" type="warning" :closable="false" />
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <el-select v-if="items.length" v-model="selected" aria-label="保活规则"><el-option v-for="(item, index) in items" :key="index" :value="index" :label="`规则 ${index + 1} · ${item.enabled ? '开启' : '关闭'}`" /></el-select>
  <el-empty v-else description="尚无缓存保活规则" />
  <el-form v-if="current" label-position="top" :disabled="busy" class="panel section-gap">
    <div class="section-toolbar"><el-switch v-model="current.enabled" active-text="启用保活" /><el-button type="danger" link @click="items.splice(selected, 1); selected = Math.max(0, selected - 1)">移除规则</el-button></div>
    <div class="form-two-columns"><el-form-item label="生活线"><el-select v-model="current.instance_id" filterable><el-option v-for="item in state?.instances ?? []" :key="item.id" :value="item.id" :label="item.display_name || item.nickname || item.peer_id" /></el-select></el-form-item>
    <el-form-item label="模型"><el-select :model-value="`${current.connection_id}:${current.model_id}`" @update:model-value="selectModel"><el-option-group v-for="connection in catalog.connections.filter(item => !item.protocol.startsWith('embedding_') && item.enabled && !item.archived)" :key="connection.id" :label="connection.name"><el-option v-for="model in connection.models" :key="model.id!" :value="`${connection.id}:${model.id}`" :label="model.label || model.model" /></el-option-group></el-select></el-form-item></div>
    <el-form-item label="发送结束后的空闲间隔"><div class="duration-fields"><el-input-number v-model="hours" :min="0" :max="168" controls-position="right" aria-label="小时" /><span>时</span><el-input-number v-model="minutes" :min="0" :max="59" controls-position="right" aria-label="分钟" /><span>分</span><el-input-number v-model="seconds" :min="0" :max="59" controls-position="right" aria-label="秒" /><span>秒</span></div></el-form-item>
    <el-form-item label="保活方式"><el-select v-model="current.mode" @change="current.compatibility_confirmed = false; current.enabled = false"><el-option label="未确认 / 不支持" value="unsupported" /><el-option label="Responses 原生预热" value="responses_prewarm" /><el-option label="Messages 原生预热" value="anthropic_prewarm" /><el-option label="Gemini 显式缓存 TTL" value="gemini_explicit" /><el-option label="最小输出请求" value="minimal" /></el-select></el-form-item>
    <el-checkbox v-model="current.compatibility_confirmed">已核对该端点兼容此方式，保持原工具与思考配置不会破坏目标缓存</el-checkbox>
    <div class="form-two-columns"><el-form-item label="保活开始时刻（管理时区）"><el-time-select v-model="current.active_start" start="00:00" end="23:59" step="00:01" /></el-form-item><el-form-item label="保活结束时刻（相同表示全天）"><el-time-select v-model="current.active_end" start="00:00" end="23:59" step="00:01" /></el-form-item></div>
    <el-collapse class="quiet-collapse"><el-collapse-item title="额度与停止条件" name="limits"><div class="form-two-columns"><el-form-item label="最长空闲保留（秒）"><el-input-number v-model="current.max_idle_seconds" :min="1" :max="604800" /></el-form-item><el-form-item label="滚动 24 小时请求上限"><el-input-number v-model="current.daily_cap" :min="1" :max="1000" /></el-form-item><el-form-item label="连续未命中／失败停止次数"><el-input-number v-model="current.failure_limit" :min="1" :max="10" /></el-form-item><el-form-item v-if="current.mode === 'gemini_explicit'" label="延长后 TTL（秒）"><el-input-number v-model="current.explicit_ttl_seconds" :min="60" :max="604800" /></el-form-item><el-form-item v-if="current.mode === 'minimal'" label="维护输出上限"><el-input-number v-model="current.output_tokens" :min="1" :max="256" /></el-form-item></div></el-collapse-item></el-collapse>
    <p class="muted">保存后等待该模型新的主叙事快照；维护仍会计费，不会发送 QQ 或执行工具。</p>
  </el-form>
  <el-table :data="state?.targets ?? []" class="section-gap"><el-table-column prop="status" label="当前状态" /><el-table-column label="下次保活"><template #default="{ row }">{{ formatTime(row.next_at, timezone) }}</template></el-table-column><el-table-column label="最近已报告命中"><template #default="{ row }">{{ formatTime(row.last_hit, timezone) }}</template></el-table-column></el-table>
  <el-collapse class="section-gap"><el-collapse-item title="最近维护记录" name="calls"><el-table :data="state?.calls ?? []"><el-table-column prop="id" label="编号" width="80" /><el-table-column label="时间"><template #default="{ row }">{{ formatTime(row.created_at, timezone) }}</template></el-table-column><el-table-column label="结果"><template #default="{ row }">{{ statuses[row.status] || row.status }}</template></el-table-column><el-table-column prop="error" label="说明" /></el-table></el-collapse-item></el-collapse>
</template>
