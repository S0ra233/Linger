<script setup lang="ts">
import { computed, onMounted, onScopeDispose, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import { protectPage, selectedLifeLine } from '../ui-state'
import EditDialog from '../components/EditDialog.vue'
import DataDetails from '../components/DataDetails.vue'
import PlanRecovery from '../components/PlanRecovery.vue'
import type { RoleInstance } from '../roleplay-types'
import type { LifeSettings, LifeOverview, LifePolicy, LifeView, LifeEvent, LifeTimeline } from '../life-types'
interface Delivery { id: string; instance_id: string; revision: number; due_at: number; text: string; status: string; parts_json: string; outbox_id: string | null }
interface Followup { id: string; instance_id: string; run_id: number; text: string; due_at: number; status: string; anchor_at: number | null; after_seconds: number; error: string | null }
const props = defineProps<{ refreshKey: number; timezone: string; instanceId?: string }>()
const selected = ref(selectedLifeLine.value), scope = computed(() => props.instanceId ?? selected.value)
const overview = ref<LifeOverview | null>(null), instances = ref<RoleInstance[]>([]), view = ref<LifeView | null>(null)
const deliveries = ref<Delivery[]>([]), followups = ref<Followup[]>([]), timeline = ref<LifeEvent[]>([]), truncated = ref(false), before = ref<number | null>(null)
const loading = ref(false), busy = ref(''), error = ref(''), updates = ref(false), tab = ref('plans'), serial = ref(0)
const settings = reactive<LifeSettings>({ enabled: false, followup_min_gap: 1, global_runs: 100, instance_runs: 24, max_unanswered: 2, grace_seconds: 900, quiet_enabled: true, quiet_start: '23:00', quiet_end: '08:00', timezone: 'Asia/Shanghai', revision: 0 })
const policy = reactive<LifePolicy>({ enabled: false, revision: 0 })
const settingsBase = ref(JSON.stringify(settings))
const dirty = computed(() => JSON.stringify(settings) !== settingsBase.value)
protectPage(() => !!busy.value || saving.value)
const editor = ref<InstanceType<typeof EditDialog> | null>(null), editing = ref(false), saving = ref(false), editorError = ref('')
const draft = reactive({ target: '', id: '', revision: 0, at: new Date(Date.now() + 3600000), text: '' })
const detailOpen = ref(false), detail = ref<unknown>(null)
const legacy = ref<unknown[]>([]), legacyLoaded = ref(false)
async function loadLegacy() { if (legacyLoaded.value) return; const mine = version; try { const value = await api<{ items: unknown[] }>(`/roleplay/life/intents?${query()}`); if (mine === version) { legacy.value = value.items; legacyLoaded.value = true } } catch (cause) { if (mine === version) error.value = errorText(cause) } }
const statuses: Record<string, string> = { pending: '待发送', held: '待复核', sending: '发送中', succeeded: '已送达', failed: '失败', unknown: '结果未知', cancelled: '已取消' }
let version = 0
function name(id: string) { const row = instances.value.find(item => item.id === id); return row ? `${row.display_name || row.nickname || row.peer_id} · ${row.character_name}` : id }
const query = () => scope.value ? `instance_id=${encodeURIComponent(scope.value)}` : ''
async function load() {
  if (busy.value) return
  const mine = ++version, id = scope.value; loading.value = true
  try {
    const [state, lines, plans, history, current] = await Promise.all([
      api<LifeOverview>('/roleplay/life'), api<RoleInstance[]>('/roleplay/instances'),
      api<{ deliveries: Delivery[]; followups: Followup[]; truncated: boolean }>(`/roleplay/life/plans?${query()}`),
      api<LifeTimeline>(`/roleplay/life/timeline?${query()}`),
      id ? api<LifeView>(`/roleplay/life/instances/${id}`) : Promise.resolve(null),
    ])
    if (mine !== version) return
    overview.value = state; instances.value = lines; deliveries.value = plans.deliveries; followups.value = plans.followups; truncated.value = plans.truncated
    timeline.value = history.items; before.value = history.next_before; view.value = current
    if (!dirty.value) { Object.assign(settings, state.settings); settingsBase.value = JSON.stringify(settings) }
    Object.assign(policy, current?.policy ?? { enabled: false, revision: 0 })
    updates.value = false; error.value = ''; serial.value++
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function select(value: string) {
  if (loading.value || busy.value || editing.value) return
  selected.value = value; selectedLifeLine.value = value; detailOpen.value = false; legacyLoaded.value = false; legacy.value = []
  await load()
}
async function saveSettings() {
  if (busy.value) return
  busy.value = 'settings'
  try {
    const value = await api<LifeSettings>('/roleplay/life/settings', { method: 'PUT', body: JSON.stringify(settings) })
    Object.assign(settings, value); settingsBase.value = JSON.stringify(settings)
    if (overview.value) overview.value.settings = value
    error.value = ''; updates.value = true; serial.value++
    ElMessage.success('投递设置已保存')
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function toggleSettings(next: string | number | boolean) {
  if (busy.value || loading.value || !overview.value) return
  const enabled = !!next
  busy.value = 'toggle'
  const previous = overview.value.settings
  try {
    const value = await api<LifeSettings>('/roleplay/life/settings', { method: 'PUT', body: JSON.stringify({ ...previous, enabled }) })
    // 只提交已保存参数和本次开关；未保存的表单参数继续保留。
    if (settings.revision === previous.revision) {
      settings.enabled = value.enabled; settings.revision = value.revision
      settingsBase.value = JSON.stringify({ ...JSON.parse(settingsBase.value), enabled: value.enabled, revision: value.revision })
    }
    overview.value.settings = value
    error.value = ''; updates.value = true; serial.value++
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function savePolicy(next: string | number | boolean) {
  if (busy.value || !scope.value) return
  const enabled = !!next
  busy.value = 'policy'
  try {
    const value = await api<LifePolicy>(`/roleplay/life/instances/${scope.value}/policy`, { method: 'PUT', body: JSON.stringify({ ...policy, enabled }) })
    Object.assign(policy, value)
    if (view.value) view.value.policy = value
    error.value = ''; updates.value = true; serial.value++
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
function openEditor(row?: Delivery) {
  if (busy.value || (!row && !scope.value)) return
  Object.assign(draft, { target: row?.instance_id || scope.value, id: row?.id || '', revision: row?.revision || 0,
    at: new Date(row ? row.due_at * 1000 : Date.now() + 3600000), text: row?.text || '' })
  editorError.value = ''; editing.value = true
}
async function saveDelivery() {
  if (saving.value || !draft.at || !draft.text.trim()) return
  saving.value = true
  try {
    await api(`/roleplay/life/instances/${draft.target}/deliveries`, { method: 'POST', body: JSON.stringify({ operation: draft.id ? 'update' : 'add', ...(draft.id ? { id: draft.id, revision: draft.revision } : {}), at: draft.at.getTime() / 1000, text: draft.text }) })
    editing.value = false; await load(); ElMessage.success('长期投递已保存')
  } catch (cause) { editorError.value = errorText(cause) }
  finally { saving.value = false }
}
async function act(row: Delivery, operation: 'keep' | 'cancel') {
  if (busy.value) return
  busy.value = row.id
  try {
    if (operation === 'cancel') await ElMessageBox.confirm('取消这项长期投递？原记录仍可查阅。', '取消投递', { confirmButtonText: '取消投递', cancelButtonText: '返回' })
    await api(`/roleplay/life/instances/${row.instance_id}/deliveries`, { method: 'POST', body: JSON.stringify({ operation, id: row.id, revision: row.revision }) })
    busy.value = ''; await load()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') error.value = errorText(cause) }
  finally { busy.value = '' }
}
async function stop(row: Followup) {
  if (busy.value) return
  busy.value = row.id
  try { await api(`/roleplay/runtime/deliveries/${row.id}/cancel`, { method: 'POST', body: '{}' }); busy.value = ''; await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = '' }
}
function hasMedia(row: Delivery) { try { return (JSON.parse(row.parts_json) as { type: string }[]).some(part => part.type === 'asset') } catch { return true } }
function eventText(row: LifeEvent) { try { const value = JSON.parse(row.data_json); return String(value.life_fragment || value.text || value.reason || value.error || '') } catch { return '' } }
async function more() {
  if (!before.value || loading.value || busy.value) return
  const mine = version, cursor = before.value; loading.value = true
  try { const value = await api<LifeTimeline>(`/roleplay/life/timeline?before=${cursor}&${query()}`); if (mine === version) { timeline.value.push(...value.items); before.value = value.next_before } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
watch(selectedLifeLine, value => { if (!props.instanceId && value !== selected.value) void select(value) })
watch(() => props.instanceId, () => { detailOpen.value = false; legacyLoaded.value = false; legacy.value = []; version++; void load() })
watch(() => props.refreshKey, () => { updates.value = true })
onMounted(() => load()); onScopeDispose(() => { version++ })
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><el-select v-if="!instanceId" :model-value="selected" placeholder="全部生活线" clearable filterable :disabled="loading || !!busy || editing" @update:model-value="select"><el-option v-for="line in instances" :key="line.id" :value="line.id" :label="name(line.id)" /></el-select><span v-else></span><el-button :loading="loading" :disabled="!!busy || editing" @click="load()">{{ updates ? '有更新 · 刷新' : '刷新' }}</el-button></div>
  <section v-if="overview && !instanceId" class="status-strip"><span>生活运行</span><el-switch :model-value="overview.settings.enabled" :disabled="!!busy || loading" :loading="busy === 'toggle'" active-text="开启" inactive-text="关闭" @change="toggleSettings" /></section>
  <section v-if="view" class="panel section-gap"><el-switch :model-value="policy.enabled" :disabled="!!busy || loading" :loading="busy === 'policy'" active-text="允许此生活线主动投递" @change="savePolicy" /><div class="check-metadata section-gap"><span>连续未回复 {{ view.context.unanswered_contacts }} / {{ settings.max_unanswered }}</span><span>24 小时投递 {{ view.context.runs_24h.instance }} / {{ settings.instance_runs }}</span><span>{{ view.context.contact_blocked.join('；') }}</span></div></section>
  <el-tabs v-model="tab" class="section-gap">
    <el-tab-pane label="投递计划" name="plans"><el-alert v-if="truncated" title="这里只显示 200 项，请选择具体生活线查看。" type="info" :closable="false" class="form-alert" />
      <div class="section-toolbar"><h2>长期投递</h2><el-button type="primary" :disabled="!scope || !!busy || loading" @click="openEditor()">新建文字投递</el-button></div>
      <section class="panel table-scroll"><el-table :data="deliveries" empty-text="尚无长期投递"><el-table-column v-if="!scope" label="生活线" min-width="150"><template #default="{ row }">{{ name(row.instance_id) }}</template></el-table-column><el-table-column prop="text" label="内容" min-width="220" show-overflow-tooltip /><el-table-column label="时间" min-width="170"><template #default="{ row }">{{ formatTime(row.due_at, timezone) }}</template></el-table-column><el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'held' || row.status === 'unknown' ? 'warning' : row.status === 'succeeded' ? 'success' : 'info'" effect="plain">{{ statuses[row.status] || row.status }}</el-tag></template></el-table-column><el-table-column label="操作" min-width="190"><template #default="{ row }"><el-button link @click="detail = row; detailOpen = true">详情</el-button><el-button v-if="row.status === 'held'" link :disabled="!!busy" @click="act(row, 'keep')">保留</el-button><el-button v-if="['pending', 'held'].includes(row.status) && !hasMedia(row)" link :disabled="!!busy" @click="openEditor(row)">修改</el-button><el-button v-if="['pending', 'held'].includes(row.status)" link :disabled="!!busy" @click="act(row, 'cancel')">取消</el-button></template></el-table-column></el-table></section>
      <h2 class="section-gap">短期分支</h2><section class="panel table-scroll"><el-table :data="followups" empty-text="尚无待执行分支"><el-table-column v-if="!scope" label="生活线" min-width="150"><template #default="{ row }">{{ name(row.instance_id) }}</template></el-table-column><el-table-column prop="text" label="内容" min-width="220" show-overflow-tooltip /><el-table-column label="时间" min-width="170"><template #default="{ row }">{{ row.anchor_at === null ? `回复送达后 ${row.after_seconds} 秒` : formatTime(row.due_at, timezone) }}</template></el-table-column><el-table-column label="状态" width="110"><template #default="{ row }">{{ statuses[row.status] || row.status }}</template></el-table-column><el-table-column label="操作" width="100"><template #default="{ row }"><el-button v-if="row.status === 'pending'" link :disabled="!!busy" @click="stop(row)">停止后续</el-button></template></el-table-column></el-table></section>
    </el-tab-pane>
    <el-tab-pane label="计划回收" name="recycle" lazy><PlanRecovery :instance-id="scope || undefined" :refresh-key="refreshKey + serial" :timezone="timezone" /></el-tab-pane>
    <el-tab-pane label="生活记录" name="timeline"><el-collapse class="quiet-collapse" @change="loadLegacy"><el-collapse-item title="历史意图（已停用）" name="legacy"><DataDetails :value="legacy" /></el-collapse-item></el-collapse><el-empty v-if="!timeline.length" description="尚无生活记录" /><section v-for="row in timeline" :key="row.id" class="panel section-gap"><div class="section-toolbar"><span>{{ formatTime(row.created_at, timezone) }}</span><el-button link @click="detail = row; detailOpen = true">详情</el-button></div><p v-if="eventText(row)" class="message-text">{{ eventText(row) }}</p><span v-else class="check-metadata">计划或设置变更</span></section><el-button v-if="before" class="section-gap" :loading="loading" @click="more">更早记录</el-button></el-tab-pane>
    <el-tab-pane v-if="!instanceId" label="投递设置" name="settings"><section class="panel"><el-form label-position="top" :disabled="!!busy || loading" @submit.prevent="saveSettings"><div class="form-two-columns"><el-form-item label="短期分支最小间隔（秒）"><el-input-number v-model="settings.followup_min_gap" :min="1" :max="3600" /></el-form-item><el-form-item label="连续未回复联系上限"><el-input-number v-model="settings.max_unanswered" :min="0" :max="20" /></el-form-item><el-form-item label="单生活线投递组／24 小时"><el-input-number v-model="settings.instance_runs" :min="0" :max="1000" /></el-form-item><el-form-item label="全局投递组／24 小时"><el-input-number v-model="settings.global_runs" :min="0" :max="10000" /></el-form-item></div><el-form-item label="安静时段"><el-switch v-model="settings.quiet_enabled" /><div v-if="settings.quiet_enabled" class="form-two-columns section-gap"><el-input v-model="settings.quiet_start" type="time" aria-label="安静时段开始" /><el-input v-model="settings.quiet_end" type="time" aria-label="安静时段结束" /></div></el-form-item><el-collapse class="quiet-collapse"><el-collapse-item title="高级设置" name="advanced"><div class="form-two-columns"><el-form-item label="时区"><el-input v-model="settings.timezone" /></el-form-item><el-form-item label="到期宽限（秒）"><el-input-number v-model="settings.grace_seconds" :min="30" :max="3600" /></el-form-item></div><p class="check-metadata">一组短期分支计一次联系，长期投递各计一次；重启不补发错过的内容。</p></el-collapse-item></el-collapse><div class="form-actions section-gap"><el-button type="primary" native-type="submit" :loading="busy === 'settings'" :disabled="JSON.stringify(settings) === settingsBase">保存设置</el-button></div></el-form></section></el-tab-pane>
  </el-tabs>
  <EditDialog ref="editor" v-model="editing" :draft="draft" :busy="saving" :title="draft.id ? '修改长期投递' : '新建长期投递'" width="600px"><el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" /><el-form label-position="top" @submit.prevent="saveDelivery"><el-form-item label="发送时间（本机时区）"><el-date-picker v-model="draft.at" type="datetime" /></el-form-item><el-form-item label="内容"><el-input v-model="draft.text" type="textarea" :rows="5" maxlength="2000" /></el-form-item><div class="form-actions"><el-button @click="editor?.close()">取消</el-button><el-button native-type="submit" type="primary" :loading="saving" :disabled="!draft.text.trim() || !draft.at">保存</el-button></div></el-form></EditDialog>
  <el-drawer v-model="detailOpen" title="记录详情" size="min(680px, 100vw)" append-to-body><DataDetails :value="detail" /></el-drawer>
</template>
