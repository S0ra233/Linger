<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import DataDetails from '../components/DataDetails.vue'
import type { Library } from '../information-types'
import type { RoleInstance } from '../roleplay-types'
import { selectedLifeLine } from '../ui-state'

interface IndexJob { id: string; space_id: string; scope_kind: string; scope_id: string; status: string; processed: number; saved: number; skipped: number; max_chunks: number; error: string | null; created_at: number }
interface IndexCall { id: number; job_id: string | null; run_id: number | null; purpose: string; status: string; usage_json: string | null; error: string | null; created_at: number }
interface IndexStatus { current: { space_id: string; connection_name: string; model: string; connection_revision: number; dimensions: number | null; coverage?: { chunks: number; indexed: number } } | null; message: string | null; jobs: IndexJob[]; calls: IndexCall[] }
const props = defineProps<{ refreshKey: number; timezone: string }>()
const kind = ref<'library' | 'memory'>('library'), target = ref(''), maximum = ref(200), batch = ref(4)
const libraries = ref<Library[]>([]), instances = ref<RoleInstance[]>([]), status = ref<IndexStatus | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), tab = ref('jobs')
const options = computed(() => kind.value === 'library' ? libraries.value.map(item => ({ id: item.id, name: item.name, disabled: !item.enabled })) : instances.value.map(item => ({ id: item.id, name: `${item.display_name || item.nickname || item.peer_id} · ${item.character_name} · ${item.account_name}`, disabled: false })))
const active = (value: string) => ['queued', 'running'].includes(value)
const labels: Record<string, string> = { cancelling: '取消中', queued: '等待中', running: '建立中', succeeded: '已完成本次任务', failed: '失败', cancelled: '已取消', interrupted: '中断／结果未知', requesting: '请求中' }
let version = 0
function scopeName(job: IndexJob) {
  if (job.scope_kind === 'library') return libraries.value.find(item => item.id === job.scope_id)?.name || job.scope_id
  const row = instances.value.find(item => item.id === job.scope_id)
  return row ? `${row.display_name || row.nickname || row.peer_id} · ${row.character_name} · ${row.account_name}` : job.scope_id
}
async function load() {
  const mine = ++version; loading.value = true
  try {
    const query = target.value ? `?scope_kind=${kind.value}&scope_id=${encodeURIComponent(target.value)}` : ''
    const [value, books, lines] = await Promise.all([api<IndexStatus>(`/information/indexes${query}`), api<Library[]>('/information/libraries'), api<RoleInstance[]>('/roleplay/instances')])
    if (mine === version) { status.value = value; libraries.value = books; instances.value = lines; error.value = '' }
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function build() {
  if (busy.value || !target.value || !status.value?.current) return
  const body = { scope_kind: kind.value, scope_id: target.value, max_chunks: maximum.value, batch_size: batch.value,
    space_id: status.value.current.space_id, connection_revision: status.value.current.connection_revision }
  const name = options.value.find(item => item.id === body.scope_id)?.name ?? body.scope_id
  busy.value = true
  try {
    await ElMessageBox.confirm(`将把“${name}”尚未索引的有效片段发送到当前 Embedding 接口，本次最多 ${body.max_chunks} 段，可能产生费用。`, '建立语义索引', { confirmButtonText: '开始建立', cancelButtonText: '取消', closeOnClickModal: false })
    await api('/information/indexes/jobs', { method: 'POST', body: JSON.stringify(body) })
    ElMessage.success('索引任务已创建'); await load()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') error.value = errorText(cause) }
  finally { busy.value = false }
}
async function cancel(job: IndexJob) {
  busy.value = true
  try { await api(`/information/indexes/jobs/${job.id}/cancel`, { method: 'POST' }); await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
watch(kind, () => { target.value = kind.value === 'memory' ? selectedLifeLine.value : '' })
watch([kind, target], () => { status.value = null; void load() }); watch(() => props.refreshKey, load); onMounted(load)
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <section class="panel" v-loading="loading && !status">
    <div class="section-toolbar"><h2>语义索引</h2><el-button :loading="loading" @click="load">刷新</el-button></div>
    <el-alert v-if="status?.message" :title="status.message" type="info" :closable="false" class="form-alert" />
    <div v-if="status?.current" class="section-toolbar"><span>{{ status.current.connection_name }} / {{ status.current.model }}</span><el-tag effect="plain">{{ status.current.dimensions == null ? '维度待建立后确认' : `${status.current.dimensions} 维` }}</el-tag></div>
    <el-form label-position="top" :disabled="busy" @submit.prevent="build">
      <div class="form-two-columns"><el-form-item label="资料类型"><el-radio-group v-model="kind"><el-radio-button value="library">共享资料库</el-radio-button><el-radio-button value="memory">私有记忆</el-radio-button></el-radio-group></el-form-item><el-form-item label="建立范围"><el-select v-model="target" filterable clearable placeholder="选择范围"><el-option v-for="item in options" :key="item.id" :value="item.id" :label="item.name" :disabled="item.disabled" /></el-select></el-form-item></div>
      <div v-if="status?.current?.coverage" class="status-strip"><span>当前空间已索引 {{ status.current.coverage.indexed }} / {{ status.current.coverage.chunks }} 个有效片段</span><span>{{ !status.current.coverage.chunks ? '暂无有效片段' : status.current.coverage.indexed === status.current.coverage.chunks ? '当前范围已齐全' : '有片段待建立' }}</span></div>
      <el-collapse class="quiet-collapse"><el-collapse-item title="本次任务限制" name="limits"><div class="form-two-columns"><el-form-item label="最多处理片段"><el-input-number v-model="maximum" :min="1" :max="5000" /></el-form-item><el-form-item label="每批片段数"><el-input-number v-model="batch" :min="1" :max="8" /></el-form-item></div></el-collapse-item></el-collapse>
      <div class="form-actions"><a href="#models">配置 Embedding 用途</a><span class="action-spacer"></span><el-button type="primary" native-type="submit" :loading="busy" :disabled="!target || !status?.current || !!status.message || status.jobs.some(job => active(job.status))">补齐当前范围索引</el-button></div>
    </el-form>
  </section>
  <el-tabs v-model="tab" class="management-tabs section-gap"><el-tab-pane label="最近任务" name="jobs"><section class="panel table-scroll"><el-table :data="status?.jobs ?? []" empty-text="尚未建立索引任务"><el-table-column label="范围" min-width="190"><template #default="{ row }">{{ scopeName(row) }}<div class="check-metadata">{{ row.scope_kind === 'library' ? '资料库' : '私有记忆' }}</div></template></el-table-column><el-table-column label="状态" min-width="130"><template #default="{ row }">{{ labels[row.status] || row.status }}</template></el-table-column><el-table-column label="进度" min-width="160"><template #default="{ row }">处理 {{ row.processed }} / {{ row.max_chunks }}<div class="check-metadata">保存 {{ row.saved }} · 跳过 {{ row.skipped }}</div></template></el-table-column><el-table-column label="时间" min-width="175"><template #default="{ row }">{{ formatTime(row.created_at, timezone) }}</template></el-table-column><el-table-column label="诊断" min-width="200"><template #default="{ row }"><span v-if="row.space_id !== status?.current?.space_id">其他模型空间</span><p v-if="row.error" class="check-error">{{ row.error }}</p></template></el-table-column><el-table-column label="操作" width="90"><template #default="{ row }"><el-button v-if="active(row.status)" link :disabled="busy" @click="cancel(row)">取消</el-button></template></el-table-column></el-table></section></el-tab-pane><el-tab-pane label="Embedding 调用" name="calls"><section class="panel"><el-empty v-if="!status?.calls.length" description="暂无调用" /><article v-for="call in status?.calls ?? []" :key="call.id" class="check-record"><div class="section-toolbar"><strong>#{{ call.id }} · {{ call.purpose === 'document' ? '建立索引' : '查询向量' }}</strong><el-tag effect="plain">{{ labels[call.status] || call.status }}</el-tag></div><p class="check-metadata">{{ formatTime(call.created_at, timezone) }}{{ call.run_id ? ' · RP 运行 #' + call.run_id : '' }} · {{ call.usage_json ? '用量与尝试记录' : '用量未知' }}</p><DataDetails v-if="call.usage_json" :value="call.usage_json" decode /><p v-if="call.error" class="check-error">{{ call.error }}</p></article></section></el-tab-pane></el-tabs>
</template>
