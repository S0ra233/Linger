<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { protectPage } from '../ui-state'
import { api, errorText, formatTime } from '../api'
import { checkLabels, purposes } from '../model-types'
import type { Assignments, ModelCatalog, ModelCheck, Purpose, SavedConnection } from '../model-types'
import ConnectionEditor from '../components/ConnectionEditor.vue'
import CacheKeepalivePanel from '../components/CacheKeepalivePanel.vue'
import ExplicitCachePanel from '../components/ExplicitCachePanel.vue'
import DataDetails from '../components/DataDetails.vue'
import RequestToolPolicy from '../components/RequestToolPolicy.vue'
import NavIcon from '../components/NavIcon.vue'
const checksVisible = ref(false), checkHistoryConnection = ref('')
const visibleChecks = computed(() => checks.value.filter(item => item.connection_id === checkHistoryConnection.value))
const cachePanel = ref<InstanceType<typeof CacheKeepalivePanel> | null>(null)
async function beforeTabLeave(_: string | number, active: string | number) { return active !== 'cache' || !cachePanel.value || await cachePanel.value.allowLeave() }

const props = defineProps<{ refreshKey: number; timezone: string }>()
const catalog = ref<ModelCatalog>({ connections: [], assignments: { revision: 0, narrator: null, summary: null, vision: null, embedding: null }, protocols: {} })
const loading = ref(false), savingAssignments = ref(false), error = ref(''), tab = ref<'connections' | 'assignments' | 'cache'>('connections'), archived = ref(false)
const editorOpen = ref(false), editing = ref<SavedConnection | null>(null)
const assignmentValues = reactive<Record<Purpose, string>>({ narrator: '', summary: '', vision: '', embedding: '' })
const baseline = ref(''), assignmentRevision = ref(0), stale = ref(false)
const assignmentDirty = computed(() => JSON.stringify(assignmentValues) !== baseline.value)
protectPage(() => savingAssignments.value)
const checks = ref<ModelCheck[]>([]), checkOpen = ref(false), checking = ref(false)
const checkError = ref('')
const search = ref(''), archiving = ref(''), detailVisible = ref(false), detailId = ref('')
const pendingDetailEdit = ref('')
const filteredConnections = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return catalog.value.connections.filter(connection => !query || [connection.name, connection.endpoint_url,
    catalog.value.protocols[connection.protocol] ?? connection.protocol,
    ...connection.models.flatMap(model => [model.label, model.model]),
  ].some(value => value.toLocaleLowerCase().includes(query)))
})
const detailConnection = computed(() => catalog.value.connections.find(connection => connection.id === detailId.value))
let loadVersion = 0
const checkConnection = ref<SavedConnection | null>(null), checkModel = ref(''), checkStream = ref(true)
const checkKind = ref<'connection' | 'plan'>('connection')
const selectedCheckModel = computed(() => checkConnection.value?.models.find(model => model.id === checkModel.value))
const canCheckPlan = computed(() => !!checkConnection.value && !checkConnection.value.protocol.startsWith('embedding_') && selectedCheckModel.value?.tool_input === true)
const pending = (status: string) => ['queued', 'running', 'cancelling'].includes(status)
const checkType = (status: string) => status === 'succeeded' ? 'success' : status === 'failed' || status === 'incomplete' ? 'danger' : 'info'
function setAssignments(data: Assignments) {
  for (const purpose of purposes) { const binding = data[purpose.id]; assignmentValues[purpose.id] = binding ? `${binding.connection_id}:${binding.model_id}` : '' }
  assignmentRevision.value = data.revision; baseline.value = JSON.stringify(assignmentValues); stale.value = false
}
async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const [data, records] = await Promise.all([
      api<ModelCatalog>(`/models/catalog?archived=${archived.value}`), api<ModelCheck[]>('/models/checks'),
    ])
    if (version !== loadVersion) return
    catalog.value = data
    if (!assignmentDirty.value || !baseline.value) setAssignments(data.assignments)
    else if (data.assignments.revision !== assignmentRevision.value) stale.value = true
    checks.value = records
    error.value = ''
  } catch (cause) { if (version === loadVersion) error.value = errorText(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
function reloadAssignments() { baseline.value = ''; void load() }
function openEditor(connection: SavedConnection | null) { editing.value = connection; editorOpen.value = true }
function editDetail() {
  const connection = detailConnection.value
  if (!connection || connection.archived || archiving.value) return
  pendingDetailEdit.value = connection.id
  detailVisible.value = false
}
async function afterDetailClosed() {
  const id = pendingDetailEdit.value
  pendingDetailEdit.value = ''
  if (!id) return
  // 等详情层退出并归还焦点后再打开整页编辑，避免关闭动画盖住新页面。
  await nextTick()
  const connection = catalog.value.connections.find(item => item.id === id)
  if (connection && !connection.archived && !archiving.value) openEditor(connection)
}
async function saved() { editorOpen.value = false; ElMessage.success('连接已保存'); await load() }
async function archive(connection: SavedConnection) {
  if (archiving.value) return
  archiving.value = connection.id
  try {
    try { await ElMessageBox.confirm(connection.archived ? '恢复这个连接？' : '归档后连接将不再用于新运行。', connection.archived ? '恢复连接' : '归档连接', { confirmButtonText: '确定', cancelButtonText: '取消' }) }
    catch { return }
    await api(`/models/connections/${connection.id}/${connection.archived ? 'restore' : 'archive'}`, { method: 'POST', body: JSON.stringify({ revision: connection.revision }) })
    await load()
  } catch (cause) { error.value = errorText(cause) }
  finally { archiving.value = '' }
}
function connectionAction(command: string, connection: SavedConnection) {
  if (command === 'details') { detailId.value = connection.id; detailVisible.value = true }
  else if (command === 'checks') openCheckRecords(connection.id)
  else if (command === 'plan') openCheck(connection, 'plan')
  else if (command === 'archive') void archive(connection)
}
function available(purpose: Purpose) {
  return catalog.value.connections.filter(connection => !connection.archived).flatMap(connection =>
    connection.models.filter(model => (purpose === 'embedding') === connection.protocol.startsWith('embedding_') && (purpose !== 'vision' || model.image_input === true)).map(model => ({
      value: `${connection.id}:${model.id}`, label: `${connection.name} / ${model.label || model.model}`, disabled: !connection.enabled,
    })))
}
async function saveAssignments() {
  savingAssignments.value = true
  const body: Record<string, unknown> = { revision: assignmentRevision.value }
  for (const purpose of purposes) {
    const [connection_id, model_id] = (assignmentValues[purpose.id] || '').split(':')
    body[purpose.id] = connection_id ? { connection_id, model_id } : null
  }
  try {
    setAssignments(await api<Assignments>('/models/assignments', { method: 'PUT', body: JSON.stringify(body) }))
    ElMessage.success('用途分配已保存')
  } catch (cause) { error.value = errorText(cause) }
  finally { savingAssignments.value = false }
}
function openCheck(connection: SavedConnection, kind: 'connection' | 'plan' = 'connection') {
  checkError.value = ''; checkConnection.value = connection
  checkModel.value = (kind === 'plan' ? connection.models.find(model => model.tool_input === true) : connection.models[0])?.id ?? connection.models[0]?.id ?? ''
  checkKind.value = kind; checkOpen.value = true
}
function openCheckRecords(id: string) { checkHistoryConnection.value = id; checksVisible.value = true }
async function startCheck() {
  if (!checkConnection.value || !checkModel.value || checking.value) return
  const connection = checkConnection.value
  checking.value = true; checkError.value = ''
  try {
    const record = await api<ModelCheck>(`/models/connections/${connection.id}/checks`, { method: 'POST',
      body: JSON.stringify({ model_id: checkModel.value, revision: connection.revision, stream: checkStream.value, kind: checkKind.value }) })
    checks.value = [record, ...checks.value.filter(item => item.id !== record.id)]
    // 检查记录已迁到抽屉；不能再切换到已移除的checks页签。
    checkOpen.value = false; openCheckRecords(connection.id); await load()
  } catch (cause) { checkError.value = errorText(cause) }
  finally { checking.value = false }
}
async function cancel(check: ModelCheck) {
  try { await api(`/models/checks/${check.id}/cancel`, { method: 'POST' }); await load() }
  catch (cause) { error.value = errorText(cause) }
}
function connectionName(id: string) { return catalog.value.connections.find(connection => connection.id === id)?.name ?? '已归档连接' }
function modelName(check: ModelCheck) { return catalog.value.connections.find(connection => connection.id === check.connection_id)?.models.find(model => model.id === check.model_id)?.model ?? check.model_id }
function usage(check: ModelCheck) {
  const value = check.result?.usage
  if (!value) return '用量未知'
  const input = value.input_tokens ?? value.prompt_tokens ?? value.promptTokenCount
  const output = value.output_tokens
  return `输入 ${input ?? '未知'}${check.result?.dimensions ? '' : ` / 输出 ${output ?? '未知'}`}`
}
watch(() => props.refreshKey, load)
watch(archived, load)
onMounted(load)
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="form-alert" />
  <el-tabs v-model="tab" class="management-tabs" :before-leave="beforeTabLeave">
    <el-tab-pane label="连接" name="connections">
      <div class="section-toolbar connection-toolbar">
        <div><el-input v-model="search" class="connection-search" placeholder="搜索连接或模型" aria-label="搜索连接或模型" clearable /><el-checkbox v-model="archived">显示归档</el-checkbox></div>
        <el-button type="primary" @click="openEditor(null)">新建连接</el-button>
      </div>
      <el-skeleton v-if="loading && !catalog.connections.length" :rows="5" animated />
      <div v-else-if="error && !catalog.connections.length" class="collection-empty"><el-button @click="load">重新载入</el-button></div>
      <el-empty v-else-if="!catalog.connections.length" description="尚未添加模型连接" :image-size="64"><el-button type="primary" @click="openEditor(null)">添加连接</el-button></el-empty>
      <el-empty v-else-if="!filteredConnections.length" description="没有匹配的连接" :image-size="64"><el-button @click="search = ''">清除搜索</el-button></el-empty>
      <section v-else class="connection-register" aria-label="模型连接" :aria-busy="loading">
        <div class="connection-register-heading" aria-hidden="true"><span>连接</span><span>协议</span><span>模型数</span><span>密钥</span><span>最近检查</span><span>操作</span></div>
        <div role="list">
          <article v-for="connection in filteredConnections" :key="connection.id" role="listitem" class="connection-row">
            <div class="connection-identity"><h2>{{ connection.name }}</h2><el-tag v-if="!connection.enabled || connection.archived" size="small" type="info" effect="plain">{{ connection.archived ? '已归档' : '停用' }}</el-tag></div>
            <div class="connection-facts">
              <div class="connection-field connection-protocol"><span class="field-caption">协议</span>{{ catalog.protocols[connection.protocol] ?? connection.protocol }}</div>
              <div class="connection-field connection-model-count"><span class="field-caption">模型</span>{{ connection.models.length }}</div>
              <div class="connection-field connection-credential"><span class="field-caption">密钥</span>{{ connection.key_configured ? '已保存' : '未配置' }}</div>
            </div>
            <div class="connection-check-summary">
              <template v-if="connection.latest_check"><el-tag size="small" effect="plain" :type="checkType(connection.latest_check.status)">{{ checkLabels[connection.latest_check.status] ?? connection.latest_check.status }}</el-tag><time>{{ formatTime(connection.latest_check.created_at, timezone) }}</time></template>
              <span v-else class="muted">尚未检查</span><span v-if="connection.latest_check" class="muted">{{ connection.latest_check.result?.check_kind === 'plan' ? '计划格式' : '基础连接' }}</span>
            </div>
            <div class="connection-row-actions">
              <el-button link @click="connectionAction('details', connection)">详情</el-button>
              <el-button v-if="!connection.archived" link :disabled="!!archiving" @click="openEditor(connection)">编辑</el-button>
              <el-button v-if="!connection.archived" link :disabled="!connection.enabled || !connection.models.length || !!archiving" @click="openCheck(connection)">检查</el-button>
              <el-dropdown trigger="click" :disabled="!!archiving" @command="(command: string) => connectionAction(command, connection)">
                <el-button text :loading="archiving === connection.id" :disabled="!!archiving" :aria-label="`${connection.name}的更多操作`">更多<span aria-hidden="true">⌄</span></el-button>
                <template #dropdown><el-dropdown-menu><el-dropdown-item command="checks">检查记录</el-dropdown-item><el-dropdown-item v-if="!connection.archived && !connection.protocol.startsWith('embedding_')" command="plan" :disabled="!connection.enabled || !connection.models.some(model => model.tool_input === true)">计划格式检查</el-dropdown-item><el-dropdown-item command="archive" divided>{{ connection.archived ? '恢复连接' : '归档连接' }}</el-dropdown-item></el-dropdown-menu></template>
              </el-dropdown>
            </div>
          </article>
        </div>
      </section>
    </el-tab-pane>
    <el-tab-pane label="用途分配" name="assignments"><section class="panel compact-form"><el-alert v-if="stale" title="用途已在其他页面更新，请重新载入后保存。" type="warning" :closable="false" class="form-alert" /><el-form label-position="top" @submit.prevent="saveAssignments"><el-form-item v-for="purpose in purposes" :key="purpose.id" :label="purpose.label"><el-select v-model="assignmentValues[purpose.id]" clearable placeholder="未分配" :disabled="savingAssignments"><el-option v-for="option in available(purpose.id)" :key="option.value" :value="option.value" :label="option.label" :disabled="option.disabled" /></el-select></el-form-item><div class="form-actions"><el-button type="primary" native-type="submit" :loading="savingAssignments" :disabled="!assignmentDirty">保存分配</el-button><el-button @click="reloadAssignments">重新载入</el-button></div></el-form></section></el-tab-pane>
    <el-tab-pane label="缓存与保活" name="cache"><CacheKeepalivePanel v-if="tab === 'cache'" ref="cachePanel" :catalog="catalog" :timezone="timezone" /><el-collapse class="quiet-collapse section-gap"><el-collapse-item title="高级 · Gemini显式缓存对象" name="explicit"><ExplicitCachePanel v-if="tab === 'cache'" :catalog="catalog" :timezone="timezone" /></el-collapse-item></el-collapse></el-tab-pane>
  </el-tabs>
  <el-drawer v-model="checksVisible" :title="connectionName(checkHistoryConnection) + ' · 检查记录'" size="min(760px, 100vw)" class="detail-drawer"><section class="panel"><el-empty v-if="!visibleChecks.length" description="暂无连接检查" :image-size="80" /><div v-for="check in visibleChecks" :key="check.id" class="check-record"><div class="section-toolbar"><h3>{{ connectionName(check.connection_id) }} / {{ modelName(check) }}</h3><div><el-tag :type="checkType(check.status)">{{ checkLabels[check.status] ?? check.status }}</el-tag><el-button v-if="pending(check.status)" link @click="cancel(check)">取消</el-button></div></div><div class="check-metadata"><span>{{ formatTime(check.created_at, timezone) }}</span><span>{{ check.result?.check_kind === 'plan' ? '最小计划格式' : '基础连接' }}</span><span>{{ usage(check) }}</span><span v-if="check.result?.dimensions">{{ check.result.dimensions }} 维</span></div><p v-if="check.error" class="check-error">{{ check.error }}</p><RequestToolPolicy v-if="check.result?.tool_policy" :value="JSON.stringify(check.result)" /><pre v-if="check.result?.text" class="check-output">{{ check.result.text }}</pre><template v-if="check.result?.format_check"><p>{{ check.result.format_check.passed ? (check.result.format_check.output === 'json' ? '最小JSON计划通过；未执行或投递。' : '最小原生工具计划通过；未执行或投递。') : '最小计划格式未通过。' }}</p><p v-for="(issue, index) in check.result.format_check.issues" :key="index" class="check-error">{{ issue.path }}：{{ issue.message }}</p><details v-if="check.result.format_check.returned_tools.length" class="raw-details"><summary>模型返回的工具参数</summary><DataDetails :value="check.result.format_check.returned_tools" /><p class="muted">只检查固定短输入与最小格式，不代表长对话、记忆或定时计划已验收。</p></details></template><el-collapse v-if="check.result?.usage" class="quiet-collapse"><el-collapse-item title="用量详情"><dl class="detail-list"><div v-for="(value, key) in check.result.usage" :key="key"><dt>{{ key }}</dt><dd>{{ value ?? '未知' }}</dd></div></dl></el-collapse-item></el-collapse></div></section></el-drawer>
  <ConnectionEditor v-if="editorOpen" v-model:open="editorOpen" :connection="editing" :protocols="catalog.protocols" @saved="saved" @cancel="editorOpen = false" />
  <el-drawer v-model="detailVisible" :title="detailConnection?.name ?? '连接详情'" size="min(560px, 100vw)" class="detail-drawer connection-detail-drawer" destroy-on-close @closed="afterDetailClosed">
    <template #header="{ titleId }"><div class="connection-detail-heading"><span class="model-identity-icon" aria-hidden="true"><NavIcon name="models" /></span><div><h2 :id="titleId">{{ detailConnection?.name ?? '连接详情' }}</h2><span v-if="detailConnection" class="muted">{{ catalog.protocols[detailConnection.protocol] ?? detailConnection.protocol }}</span></div></div></template>
    <template v-if="detailConnection">
      <div class="connection-detail-overview"><el-tag effect="light" :type="detailConnection.archived || !detailConnection.enabled ? 'info' : 'success'">{{ detailConnection.archived ? '已归档' : detailConnection.enabled ? '已启用' : '已停用' }}</el-tag><span>{{ detailConnection.key_configured ? '密钥已保存' : '未配置密钥' }}</span></div>
      <section class="drawer-section"><h3>接口</h3><div class="endpoint-detail"><span>接口地址</span><code>{{ detailConnection.endpoint_base_url ?? detailConnection.endpoint_url }}</code></div><div v-if="detailConnection.resolved_endpoint_url !== (detailConnection.endpoint_base_url ?? detailConnection.endpoint_url)" class="endpoint-detail"><span>实际请求</span><code>{{ detailConnection.resolved_endpoint_url }}</code></div></section>
      <section class="drawer-section"><div class="drawer-section-heading"><h3>模型</h3><span class="subtle-count">{{ detailConnection.models.length }}</span></div><ul class="drawer-models"><li v-for="model in detailConnection.models" :key="model.id ?? model.model"><div><strong>{{ model.label || model.model }}</strong><span v-if="model.label && model.label !== model.model" class="mono muted">{{ model.model }}</span></div><div v-if="model.tool_input || model.image_input" class="drawer-model-capabilities"><span v-if="model.tool_input">工具</span><span v-if="model.image_input">图片</span></div></li></ul></section>
      <section class="drawer-section"><h3>最近检查</h3><template v-if="detailConnection.latest_check"><div class="check-metadata"><el-tag effect="light" :type="checkType(detailConnection.latest_check.status)">{{ checkLabels[detailConnection.latest_check.status] ?? detailConnection.latest_check.status }}</el-tag><time>{{ formatTime(detailConnection.latest_check.created_at, timezone) }}</time></div><p v-if="detailConnection.latest_check.error" class="check-error">{{ detailConnection.latest_check.error }}</p></template><p v-else class="muted">尚未检查</p></section>
    </template><el-empty v-else description="当前列表中已没有此连接" />
    <template #footer><div class="connection-detail-footer"><span v-if="detailConnection" class="muted">{{ formatTime(detailConnection.updated_at, timezone) }} 更新</span><div><el-button @click="detailVisible = false">关闭</el-button><el-button v-if="detailConnection && !detailConnection.archived" type="primary" :disabled="!!archiving" @click="editDetail">编辑连接</el-button></div></div></template>
  </el-drawer>
  <el-dialog v-model="checkOpen" title="模型检查" width="440px" class="model-dialog" :close-on-click-modal="false" :close-on-press-escape="!checking" :show-close="!checking"><el-alert v-if="checkError" :title="checkError" type="error" :closable="false" class="form-alert" /><el-form label-position="top"><el-form-item label="检查项目"><el-radio-group v-model="checkKind" :disabled="checking"><el-radio-button value="connection">基础连接</el-radio-button><el-radio-button value="plan" :disabled="!canCheckPlan">计划格式</el-radio-button></el-radio-group></el-form-item><el-form-item label="模型"><el-select v-model="checkModel" :disabled="checking"><el-option v-for="model in checkConnection?.models ?? []" :key="model.id!" :value="model.id!" :label="model.label || model.model" /></el-select></el-form-item><el-checkbox v-if="!checkConnection?.protocol.startsWith('embedding_')" v-model="checkStream" :disabled="checking">使用流式协议</el-checkbox><p class="check-cost-note">{{ checkKind === 'plan' ? '会请求一次最小工具计划，可能产生接口费用；不携带聊天或预设正文，不执行工具，也不发QQ。' : '将发送一条短请求，可能产生接口费用。' }}</p><p v-if="checkKind === 'plan' && !canCheckPlan" class="check-error">请选择已明确启用工具能力的生成模型。</p></el-form><template #footer><el-button :disabled="checking" @click="checkOpen = false">取消</el-button><el-button type="primary" :loading="checking" :disabled="!checkModel || checkKind === 'plan' && !canCheckPlan" @click="startCheck">开始检查</el-button></template></el-dialog>
</template>
