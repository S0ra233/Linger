<script setup lang="ts">
import MediaMessage from '../components/MediaMessage.vue'
import RequestBudgetDetails from '../components/RequestBudgetDetails.vue'
import RequestAttempts from '../components/RequestAttempts.vue'
import RequestToolPolicy from '../components/RequestToolPolicy.vue'
import RunExplanation from '../components/RunExplanation.vue'
import RunDiagnostics from '../components/RunDiagnostics.vue'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import DataDetails from '../components/DataDetails.vue'
import { protectPage, selectedLifeLine } from '../ui-state'
import { api, errorText, formatTime } from '../api'
import type { Delivery, RpRun, RunDetail, RunsPage, RuntimeInstance, RuntimeOverview, RuntimeSettings } from '../runtime-types'

const props = defineProps<{ refreshKey: number; timezone: string; instanceId?: string }>()
const updates = ref(false)
const overview = ref<RuntimeOverview | null>(null), runs = ref<RpRun[]>([]), deliveries = ref<Delivery[]>([])
const error = ref(''), loading = ref(false), busy = ref(''), tab = ref('sessions'), nextBefore = ref<number | null>(null)
const form = reactive<RuntimeSettings>({ enabled: false, concurrency: 2, debounce_seconds: 2, history_messages: 20, context_bytes: 64000,
  max_steps: 6, max_tool_calls: 16, run_timeout: 240, receipt_timeout: 30, delivery_grace: 300, schedule_hours: 168, hide_html_comments: true, revision: 0 })
const settingsBase = ref<RuntimeSettings | null>(null)
const dirty = computed(() => !!settingsBase.value && (Object.keys(form) as (keyof RuntimeSettings)[])
  .some(key => key !== 'revision' && key !== 'enabled' && form[key] !== settingsBase.value![key]))
const settingsError = ref(''), detailVisible = ref(false), detail = ref<RunDetail | null>(null), detailLoading = ref(false)
const expandedPanels = ref<(string | number)[]>(['decision'])
async function locateStep(stepId: number) {
  if (!detail.value?.steps.some(step => step.id === stepId)) return
  expandedPanels.value = [...new Set([...expandedPanels.value, stepId])]
  await nextTick()
  document.getElementById(`rp-step-${stepId}`)?.scrollIntoView({ block: 'start', behavior: 'auto' })
}
protectPage(() => !!busy.value)
const selected = selectedLifeLine, scope = computed(() => props.instanceId ?? selected.value)
const scopedInstances = computed(() => (overview.value?.instances ?? []).filter(item => !scope.value || item.id === scope.value))
const scopeQuery = () => scope.value ? `instance_id=${encodeURIComponent(scope.value)}` : ''
function selectScope(value: string) { if (!busy.value) selected.value = value }
const stateVisible = ref(false), stateValue = ref<unknown>(null)
const labels: Record<string, string> = { running: '生成中', completed: '计划已提交', cancelled: '已取消', failed: '失败', unknown: '结果未知', pending: '待发送', sending: '发送中', succeeded: '已送达', requesting: '请求中', returned: '已返回' }
let loadVersion = 0, detailVersion = 0, stateVersion = 0
function label(value: string) { return labels[value] ?? value }
function tagType(value: string): 'success' | 'warning' | 'danger' | 'info' {
  return value === 'succeeded' || value === 'completed' ? 'success' : value === 'unknown' || value === 'sending' ? 'warning' : value === 'failed' ? 'danger' : 'info'
}
function instanceName(id: string) {
  const row = overview.value?.instances.find(item => item.id === id)
  return row ? `${row.display_name || row.nickname || row.peer_id} · ${row.character_name}` : id
}
function pretty(value: unknown) {
  if (typeof value === 'string') { try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value } }
  return JSON.stringify(value, null, 2)
}
async function load() {
  const version = ++loadVersion
  updates.value = false; loading.value = true
  try {
    const values = await Promise.all([api<RuntimeOverview>('/roleplay/runtime'), api<RunsPage>(`/roleplay/runtime/runs?${scopeQuery()}`), api<Delivery[]>(`/roleplay/runtime/deliveries?${scopeQuery()}`)])
    if (version !== loadVersion) return
    overview.value = values[0]; deliveries.value = values[2]; error.value = ''
    if (!dirty.value) { Object.assign(form, values[0].settings); settingsBase.value = { ...values[0].settings } }
    // 已展开更早运行时保留阅读位置，只合并新记录，不静默清空。
    if (runs.value.length > 30) {
      const map = new Map(runs.value.map(item => [item.id, item]))
      values[1].items.forEach(item => map.set(item.id, item)); runs.value = [...map.values()].sort((a, b) => b.id - a.id)
    } else { runs.value = values[1].items; nextBefore.value = values[1].next_before }
  } catch (cause) { if (version === loadVersion) error.value = errorText(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
async function saveSettings() {
  if (busy.value) return
  busy.value = 'settings'; settingsError.value = ''
  try {
    const value = await api<RuntimeSettings>('/roleplay/runtime/settings', { method: 'PUT', body: JSON.stringify(form) })
    Object.assign(form, value); settingsBase.value = { ...value }
    ElMessage.success('运行设置已保存'); await load()
  } catch (cause) { settingsError.value = errorText(cause) }
  finally { busy.value = '' }
}
async function toggle() {
  if (!overview.value || busy.value) return
  busy.value = 'toggle'
  try {
    const enabled = !overview.value.settings.enabled
    const value = await api<RuntimeSettings>('/roleplay/runtime/settings', { method: 'PUT', body: JSON.stringify({ ...overview.value.settings, enabled }) })
    // 开关独立保存，不夹带尚未提交的参数，也不丢弃参数草稿。
    if (form.revision === overview.value.settings.revision) {
      form.enabled = value.enabled; form.revision = value.revision
      if (settingsBase.value) Object.assign(settingsBase.value, { enabled: value.enabled, revision: value.revision })
    }
    overview.value.settings = value
    if (!dirty.value) { Object.assign(form, value); settingsBase.value = { ...value } }
    await load()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function control(item: RuntimeInstance, action: 'pause' | 'resume' | 'retry') {
  if (busy.value) return
  busy.value = item.id
  try {
    if (action === 'retry') await ElMessageBox.confirm('将取消尚未发送的旧计划，并重新评估这条生活线的待处理输入；可能产生模型费用。', '重新评估', { confirmButtonText: '继续', cancelButtonText: '取消' })
    await api(`/roleplay/runtime/instances/${item.id}/${action}`, { method: 'POST' }); await load()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function openState(item: RuntimeInstance) {
  detailVisible.value = false; detailVersion++
  const mine = ++stateVersion, target = scope.value
  stateValue.value = null; stateVisible.value = true
  try { const value = await api(`/roleplay/runtime/instances/${item.id}/state`); if (mine === stateVersion && target === scope.value) stateValue.value = value }
  catch (cause) { if (mine === stateVersion) { stateVisible.value = false; error.value = errorText(cause) } }
}
async function openDetail(id: number) {
  stateVisible.value = false; stateVersion++
  const version = ++detailVersion
  detail.value = null; expandedPanels.value = ['decision']; detailVisible.value = true; detailLoading.value = true
  try {
    const value = await api<RunDetail>(`/roleplay/runtime/runs/${id}`)
    if (version === detailVersion) detail.value = value
  } catch (cause) { if (version === detailVersion) error.value = errorText(cause) }
  finally { if (version === detailVersion) detailLoading.value = false }
}
async function moreRuns() {
  if (!nextBefore.value || loading.value) return
  const version = loadVersion, instance = scope.value
  loading.value = true
  try {
    const value = await api<RunsPage>(`/roleplay/runtime/runs?before=${nextBefore.value}&${scopeQuery()}`)
    if (version !== loadVersion || instance !== scope.value) return
    const map = new Map(runs.value.map(item => [item.id, item])); value.items.forEach(item => map.set(item.id, item))
    runs.value = [...map.values()].sort((a, b) => b.id - a.id); nextBefore.value = value.next_before
  } catch (cause) { if (version === loadVersion) error.value = errorText(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
async function cancelDelivery(item: Delivery) {
  if (busy.value) return
  busy.value = item.id
  try { await api(`/roleplay/runtime/deliveries/${item.id}/cancel`, { method: 'POST' }); await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function resolve(item: Delivery, delivered: boolean) {
  if (busy.value) return
  busy.value = item.id
  try {
    let messageId: string | null = null
    if (delivered) {
      const value = await ElMessageBox.prompt('核对 QQ 实际记录，填写已发送消息的编号。', '人工确认已送达', { confirmButtonText: '确认', cancelButtonText: '取消', inputPattern: /^-?\d{1,20}$/, inputErrorMessage: '请填写实际消息编号' })
      messageId = value.value
    } else await ElMessageBox.confirm('请先核对 QQ 记录。确认未送达后会解除未知状态，不会直接重发原消息。', '人工确认未送达', { confirmButtonText: '确认未送达', cancelButtonText: '取消' })
    await api(`/roleplay/runtime/deliveries/${item.id}/resolve`, { method: 'POST', body: JSON.stringify({ result: delivered ? 'succeeded' : 'failed', message_id: messageId }) })
    await load()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') error.value = errorText(cause) }
  finally { busy.value = '' }
}
watch(scope, () => { loadVersion++; detailVersion++; stateVersion++; detailVisible.value = false; stateVisible.value = false; runs.value = []; deliveries.value = []; nextBefore.value = null; void load() })
watch(() => props.refreshKey, () => { updates.value = true })
onMounted(load)
</script>

<template>
  <el-alert v-if="error || overview?.summary.last_error" :title="error || overview?.summary.last_error || ''" type="error" :closable="false" class="form-alert" />
  <section v-if="overview && !instanceId" class="status-strip"><span><i :class="['quiet-dot', { offline: !overview.settings.enabled }]"></i>{{ overview.settings.enabled ? '全局自动回复已开启' : '全局自动回复已关闭' }}<el-tag effect="plain" type="info">生成 {{ overview.summary.generating }} · 投递 {{ overview.summary.sending }}</el-tag></span><el-button :type="overview.settings.enabled ? 'default' : 'primary'" :loading="busy === 'toggle'" @click="toggle">{{ overview.settings.enabled ? '关闭自动运行' : '开启自动运行' }}</el-button></section>
  <div class="section-toolbar"><span v-if="instanceId">此生活线的运行记录</span><div v-else><el-select :model-value="selected" class="account-select" filterable clearable placeholder="全部生活线" :disabled="!!busy" @update:model-value="selectScope"><el-option v-for="item in overview?.instances ?? []" :key="item.id" :value="item.id" :label="instanceName(item.id)" /></el-select><el-button v-if="selected" link @click="selectScope('')">查看全部</el-button></div><el-button :type="updates ? 'primary' : 'default'" :loading="loading" @click="load">{{ updates ? '有变化 · 刷新记录' : '刷新记录' }}</el-button></div>
  <el-tabs v-model="tab" class="management-tabs">
    <el-tab-pane label="生活线" name="sessions"><section class="panel table-scroll" v-loading="loading"><el-table :data="scopedInstances" empty-text="请先绑定联系人">
      <el-table-column label="联系人／角色" min-width="180"><template #default="{ row }"><strong>{{ instanceName(row.id) }}</strong><div class="contact-number">{{ row.account_name }}</div></template></el-table-column>
      <el-table-column label="状态" width="95"><template #default="{ row }"><el-tag :type="row.paused || !row.receiving ? 'info' : 'success'" effect="plain">{{ row.paused ? '已暂停' : row.receiving ? '已启用' : '未接收' }}</el-tag></template></el-table-column>
      <el-table-column prop="pending_inputs" label="待处理输入" width="105" />
      <el-table-column label="下次发送" min-width="170"><template #default="{ row }">{{ formatTime(row.next_delivery, timezone) }}</template></el-table-column>
      <el-table-column label="操作" min-width="210"><template #default="{ row }"><el-button link @click="openState(row)">状态</el-button><el-button link :disabled="!!busy" @click="control(row, row.paused ? 'resume' : 'pause')">{{ row.paused ? '恢复' : '暂停' }}</el-button><el-button link :disabled="!!busy || !row.pending_inputs" @click="control(row, 'retry')">重新评估</el-button></template></el-table-column>
    </el-table></section></el-tab-pane>
    <el-tab-pane label="模型运行" name="runs"><section class="panel table-scroll"><el-table :data="runs" empty-text="尚无模型运行">
      <el-table-column prop="id" label="编号" width="75" /><el-table-column label="来源" width="100"><template #default="{ row }">{{ row.trigger_kind === 'manual' ? '管理操作' : row.trigger_kind === 'life' ? '历史后台生活' : 'QQ 输入' }}</template></el-table-column><el-table-column label="生活线" min-width="180"><template #default="{ row }">{{ instanceName(row.instance_id) }}</template></el-table-column>
      <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="tagType(row.status)" effect="plain">{{ label(row.status) }}</el-tag></template></el-table-column>
      <el-table-column label="时间" min-width="175"><template #default="{ row }">{{ formatTime(row.created_at, timezone) }}</template></el-table-column>
      <el-table-column label="详情" min-width="120"><template #default="{ row }"><el-button link @click="openDetail(row.id)">查看</el-button></template></el-table-column>
    </el-table><div v-if="nextBefore" class="load-more"><el-button :loading="loading" @click="moreRuns">更早运行</el-button></div></section></el-tab-pane>
    <el-tab-pane label="投递计划" name="deliveries"><div class="connection-list"><el-empty v-if="!deliveries.length" description="暂无投递计划" /><section v-for="item in deliveries" :key="item.id" class="panel">
      <div class="connection-heading"><div><h2>{{ instanceName(item.instance_id) }}<el-tag :type="tagType(item.status)" effect="plain">{{ label(item.status) }}</el-tag></h2><div class="connection-meta"><span>计划 {{ formatTime(item.due_at, timezone) }}</span><span>运行 #{{ item.run_id }} · 气泡 {{ item.position + 1 }}</span><span v-if="item.message_id">QQ 消息 {{ item.message_id }}</span></div></div><div class="connection-actions"><el-button v-if="item.status === 'pending'" :disabled="!!busy" @click="cancelDelivery(item)">取消后续气泡</el-button><template v-if="item.status === 'unknown'"><el-button :disabled="!!busy" @click="resolve(item, true)">确认已送达</el-button><el-button :disabled="!!busy" @click="resolve(item, false)">确认未送达</el-button></template></div></div>
      <p class="delivery-caption">{{ item.status === 'succeeded' ? '已确认送达' : item.status === 'unknown' ? '结果未知，请核对 QQ' : '模型生成内容 · 尚未确认送达' }}</p><p class="message-text delivery-text">{{ item.text }}</p><MediaMessage :parts="item.parts_json" /><p v-if="item.error" class="check-error">{{ item.error }}</p>
    </section></div></el-tab-pane>
    <el-tab-pane v-if="!instanceId" label="全局运行设置" name="settings"><section class="panel compact-form"><el-alert v-if="settingsError" :title="settingsError" type="error" :closable="false" class="form-alert" />
      <el-form label-position="top" :disabled="!!busy" @submit.prevent="saveSettings">
        <div class="form-two-columns"><el-form-item label="会话并发"><el-input-number v-model="form.concurrency" :min="1" :max="8" /></el-form-item><el-form-item label="输入防抖（秒）"><el-input-number v-model="form.debounce_seconds" :min="0" :max="30" :step="0.5" /></el-form-item><el-form-item label="历史与摘要"><a href="#contacts">联系人 → 上下文</a></el-form-item><el-form-item label="发送时隐藏 HTML 草稿注释"><el-switch v-model="form.hide_html_comments" /></el-form-item></div>
        <el-collapse class="quiet-collapse"><el-collapse-item title="时限与计划范围" name="limits"><div class="form-two-columns"><el-form-item label="模型步骤上限"><el-input-number v-model="form.max_steps" :min="2" :max="12" /></el-form-item><el-form-item label="工具调用上限"><el-input-number v-model="form.max_tool_calls" :min="1" :max="32" /></el-form-item><el-form-item label="本轮总时限（秒）"><el-input-number v-model="form.run_timeout" :min="10" :max="900" /></el-form-item><el-form-item label="回执等待（秒）"><el-input-number v-model="form.receipt_timeout" :min="5" :max="120" /></el-form-item><el-form-item label="离线宽限（秒）"><el-input-number v-model="form.delivery_grace" :min="30" :max="3600" /></el-form-item><el-form-item label="最远计划时间（小时）"><el-input-number v-model="form.schedule_hours" :min="1" :max="720" /></el-form-item></div></el-collapse-item></el-collapse>
        <div class="form-actions"><el-button type="primary" native-type="submit" :loading="busy === 'settings'" :disabled="!dirty || !overview">保存设置</el-button><el-button @click="settingsBase = null; load()">重新载入</el-button></div>
      </el-form>
    </section></el-tab-pane>
  </el-tabs>
  <el-drawer v-model="detailVisible" title="运行详情" size="min(820px, 94vw)"
    class="detail-drawer" destroy-on-close @closed="detailVersion++">
    <el-skeleton v-if="detailLoading" :rows="5" animated />
    <template v-if="detail">
      <div class="section-toolbar">
        <el-tag :type="tagType(detail.run.status)">{{ label(detail.run.status) }}</el-tag>
        <el-button @click="openDetail(detail.run.id)">刷新详情</el-button>
      </div>
      <el-alert v-if="detail.run.error && !detail.diagnostics?.failure" :title="detail.run.error"
        type="warning" :closable="false" class="form-alert" />
      <dl class="detail-list">
        <div><dt>运行结果</dt><dd>{{ label(detail.run.status) }}</dd></div>
        <div><dt>触发来源</dt><dd>{{ detail.run.trigger_kind === 'manual' ? '管理操作（未调用模型）' : detail.run.trigger_kind === 'life' ? '历史到期评估' : 'QQ 输入' }}</dd></div>
        <div><dt>运行编号</dt><dd>#{{ detail.run.id }}</dd></div>
        <div><dt>创建时间</dt><dd>{{ formatTime(detail.run.created_at, timezone) }}</dd></div>
      </dl>
      <RunDiagnostics :value="detail.diagnostics" :status="detail.run.status" @locate="locateStep" />
      <RunExplanation :value="detail.explanation" :timezone="timezone" />
      <RequestBudgetDetails :value="detail.run.context_json" blocked />
      <el-collapse v-model="expandedPanels" class="section-gap">
        <el-collapse-item title="已提交计划" name="decision">
          <DataDetails :value="detail.run.decision_json" decode />
        </el-collapse-item>
        <el-collapse-item title="上下文预算与世界书" name="context">
          <pre class="diagnostic-json">{{ pretty(detail.run.context_json) }}</pre>
        </el-collapse-item>
        <el-collapse-item v-if="detail.run.mcp_json" title="外部工具授权快照" name="mcp">
          <DataDetails :value="detail.run.mcp_json" decode />
        </el-collapse-item>
        <el-collapse-item v-if="detail.run.life_json" title="生活触发与限制快照" name="life">
          <DataDetails :value="detail.run.life_json" decode />
        </el-collapse-item>
        <el-collapse-item title="模型与设置快照" name="config">
          <pre class="diagnostic-json">{{ pretty(detail.run.model_json) }}</pre>
          <pre class="diagnostic-json">{{ pretty(detail.run.settings_json) }}</pre>
        </el-collapse-item>
        <el-collapse-item v-for="step in detail.steps" :id="`rp-step-${step.id}`" :key="step.id" :name="step.id"
          :title="`步骤 ${step.step} · ${label(step.status)}`">
          <RequestBudgetDetails :value="step.response_json" />
          <RequestAttempts :value="step.response_json" :timezone="timezone" />
          <RequestToolPolicy :value="step.response_json" />
          <h3>响应与用量</h3><DataDetails :value="step.response_json" decode />
          <h3>工具结果</h3><DataDetails :value="step.tools_json" decode />
          <details class="raw-details">
            <summary>原始请求</summary><pre class="diagnostic-json">{{ pretty(step.request_json) }}</pre>
          </details>
        </el-collapse-item>
      </el-collapse>
    </template>
  </el-drawer>
  <el-drawer v-model="stateVisible" title="角色运行状态" size="min(820px, 94vw)" class="detail-drawer" destroy-on-close @closed="stateVersion++"><DataDetails v-if="stateValue !== null" :value="stateValue" /><el-skeleton v-else :rows="4" animated /></el-drawer>
</template>
