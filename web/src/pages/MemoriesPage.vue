<script setup lang="ts">
import EditDialog from '../components/EditDialog.vue'
import EvidencePicker from '../components/EvidencePicker.vue'
import DataDetails from '../components/DataDetails.vue'
import ConversationWindowLink from '../components/ConversationWindowLink.vue'
import MemoryProposals from '../components/MemoryProposals.vue'
import ForgetMemoriesButton from '../components/ForgetMemoriesButton.vue'
import { computed, onMounted, onScopeDispose, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { selectedLifeLine } from '../ui-state'
import { api, errorText, formatTime } from '../api'
import type { RoleInstance } from '../roleplay-types'
import type { CursorPage, Memory, MemoryDetail, EvidenceRef } from '../information-types'
const selected = selectedLifeLine
const memoryDialog = ref<InstanceType<typeof EditDialog> | null>(null)
const props = defineProps<{ refreshKey: number; timezone: string; embedded?: boolean }>()
const instances = ref<RoleInstance[]>([]), items = ref<Memory[]>([]), next = ref<string | null>(null)
const error = ref(''), editorError = ref(''), loading = ref(false), saving = ref(false), visible = ref(false), detailVisible = ref(false), detail = ref<MemoryDetail | null>(null)
const editing = ref<string | null>(null), editorInstance = ref('')
const sourcePicker = ref(false)
const sourceNames = { input: '用户输入', sent: '确认送达', life: '生活事件', world_event: '已采纳世界事件', observation: '公开观察' }
const empty = () => ({ kind: 'profile' as Memory['kind'], text: '', expires_at: null as number | null, status: 'active' as Memory['status'], replace_sources: false, source_message_ids: [] as number[], sources: [] as EvidenceRef[], revision: 0, supersedes: [] as { memory_id: string; revision: number }[] })
const form = reactive(empty()), expires = ref<number | null>(null), sourceText = ref('')
const validFrom = ref<number | null>(null), replacementChoices = ref<Memory[]>([])
const advancedPanels = ref<string[]>([])
const protectionOpen = ref(false), protectionBusy = ref(false), protectionError = ref(''), protectionTarget = ref<Memory | null>(null), protectionLocked = ref(true), promote = ref(false)
const kindLabels = { profile: '长期画像', temporary: '旧临时记忆', episode: '情景记忆' }
const statusLabels: Record<string, string> = { active: '有效', forgotten: '已遗忘', expired: '已过期', source_invalid: '来源失效', superseded: '已被替代' }
const updates = ref(false), forgetting = ref('')
const filter = ref('all'), displayed = computed(() => items.value.filter(item => filter.value === 'all' || item.kind === filter.value))
let version = 0, instanceVersion = 0, detailVersion = 0, protectionVersion = 0
async function loadInstances() {
  const mine = ++instanceVersion
  try { const value = await api<RoleInstance[]>('/roleplay/instances'); if (mine === instanceVersion) instances.value = value }
  catch (cause) { if (mine === instanceVersion) error.value = errorText(cause) }
}
async function load(more = false) {
  if (!selected.value) { version++; loading.value = false; return }
  const mine = ++version, id = selected.value; loading.value = true; if (!more) updates.value = false
  try { const page = await api<CursorPage<Memory>>(`/information/instances/${id}/memories${more && next.value ? '?before=' + next.value : ''}`); if (mine === version) { items.value = more ? [...items.value, ...page.items] : page.items; next.value = page.next_before; error.value = '' } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function edit(item?: Memory, replace = false) {
  editorInstance.value = selected.value; editing.value = replace ? null : item?.id ?? null; Object.assign(form, empty())
  replacementChoices.value = items.value.filter(row => row.status === 'active' && !row.superseded_by).map(row => ({ ...row }))
  if (item && !replace) Object.assign(form, { kind: item.kind === 'temporary' ? 'profile' : item.kind, text: item.text, expires_at: item.expires_at, revision: item.revision, status: 'active' })
  if (item && replace) Object.assign(form, { kind: item.kind === 'temporary' ? 'profile' : item.kind, supersedes: [{ memory_id: item.id, revision: item.revision }] })
  advancedPanels.value = replace ? ['replacement'] : []
  expires.value = !replace && item?.expires_at ? item.expires_at * 1000 : null
  validFrom.value = !replace && item?.valid_from != null ? item.valid_from * 1000 : null
  sourceText.value = ''; editorError.value = ''; visible.value = true
}
async function save() {
  if (saving.value) return
  const sources = sourceText.value.trim() ? sourceText.value.trim().split(/[\s,，]+/).map(Number) : []
  if (sources.some(id => !Number.isSafeInteger(id) || id < 1)) { editorError.value = '来源须为系统输入记录编号。'; return }
  saving.value = true; editorError.value = ''
  try { await api(`/information/instances/${editorInstance.value}/memories${editing.value ? '/' + editing.value : ''}`, { method: editing.value ? 'PUT' : 'POST', body: JSON.stringify({ ...form, expires_at: form.kind === 'temporary' && expires.value ? expires.value / 1000 : null, valid_from: validFrom.value == null ? null : validFrom.value / 1000, source_message_ids: sources }) }); visible.value = false; ElMessage.success('记忆已保存'); if (editorInstance.value === selected.value) await load() }
  catch (cause) { editorError.value = errorText(cause) }
  finally { saving.value = false }
}
async function forget(item: Memory) {
  if (forgetting.value) return
  const id = selected.value; forgetting.value = item.id
  try {
    try { await ElMessageBox.confirm('停止召回这条记忆，并取消尚未发送的旧计划。原始聊天和历史摘要保留。', '遗忘记忆', { confirmButtonText: '遗忘', cancelButtonText: '取消', type: 'warning' }) }
    catch { return }
    await api(`/information/instances/${id}/memories/${item.id}/forget`, { method: 'POST', body: JSON.stringify({ revision: item.revision }) })
    if (id === selected.value) await load()
    ElMessage.success('此记忆已停止召回，原始来源保留')
  } catch (cause) { error.value = errorText(cause) }
  finally { forgetting.value = '' }
}
async function showDetail(item: Pick<Memory, 'id'>) {
  const id = selected.value, mine = ++detailVersion; detail.value = null; detailVisible.value = true
  try { const value = await api<MemoryDetail>(`/information/instances/${id}/memories/${item.id}`); if (mine === detailVersion) detail.value = value }
  catch (cause) { if (mine === detailVersion) { error.value = errorText(cause); detailVisible.value = false } }
}
function openProtection(item: Memory) {
  protectionTarget.value = { ...item }; protectionLocked.value = !item.locked; promote.value = false
  protectionError.value = ''; protectionOpen.value = true
}
async function saveProtection() {
  const target = protectionTarget.value
  if (!target || protectionBusy.value) return
  const mine = ++protectionVersion
  protectionBusy.value = true; protectionError.value = ''
  try {
    await api(`/information/instances/${target.instance_id}/memories/${target.id}/protection`, { method: 'PUT', body: JSON.stringify({ revision: target.revision, protection_revision: target.protection_revision, locked: protectionLocked.value, promote_to_profile: protectionLocked.value && promote.value }) })
    if (mine === protectionVersion) { protectionOpen.value = false; ElMessage.success(protectionLocked.value ? '记忆已确认并锁定' : '记忆已解锁'); if (target.instance_id === selected.value) await load() }
  } catch (cause) { if (mine === protectionVersion) protectionError.value = errorText(cause) }
  finally { if (mine === protectionVersion) protectionBusy.value = false }
}
function memoryAction(command: string, item: Memory) {
  if (command === 'protection') openProtection(item)
  else if (command === 'replace') edit(item, true)
  else if (command === 'forget') void forget(item)
}
watch(selected, () => { version++; detailVersion++; protectionVersion++; protectionOpen.value = false; protectionBusy.value = false; detailVisible.value = false; items.value = []; next.value = null; error.value = ''; void load() }, { immediate: true })
watch(() => props.refreshKey, () => { updates.value = true; void loadInstances() })
onMounted(loadInstances)
onScopeDispose(() => { version++; instanceVersion++; detailVersion++; protectionVersion++ })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><el-select v-if="!embedded" v-model="selected" class="account-select" placeholder="选择生活线" filterable><el-option v-for="item in instances" :key="item.id" :value="item.id" :label="`${item.display_name || item.nickname || item.peer_id} · ${item.character_name} · ${item.account_name}`" /></el-select><div><el-button :type="updates ? 'primary' : 'default'" :disabled="!selected" :loading="loading" @click="load()">{{ updates ? '有变化 · 刷新' : '刷新' }}</el-button><el-button type="primary" :disabled="!selected" @click="edit()">新增记忆</el-button><ForgetMemoriesButton :instance-id="selected" :disabled="!!forgetting || saving" @changed="load()" /></div></div>
  <section class="panel" v-loading="loading"><el-radio-group v-model="filter"><el-radio-button value="all">全部</el-radio-button><el-radio-button v-for="(label, kind) in kindLabels" :key="kind" :value="kind">{{ label }}</el-radio-button></el-radio-group><el-empty v-if="!displayed.length" :description="selected ? '当前页没有记忆' : '先选择生活线'" />
    <article v-for="item in displayed" :key="item.id" class="check-record"><div class="section-toolbar"><div><el-tag effect="plain">{{ kindLabels[item.kind] }}</el-tag> <el-tag :type="item.effective_status === 'active' ? 'success' : 'info'" effect="plain">{{ statusLabels[item.effective_status ?? item.status] }}</el-tag></div><div><el-button link @click="showDetail(item)">来源与版本</el-button><el-button v-if="!item.superseded_by" link @click="edit(item)">{{ item.status === 'forgotten' ? '编辑／恢复' : '编辑' }}</el-button><el-dropdown v-if="!item.superseded_by" :disabled="!!forgetting" @command="(command: string) => memoryAction(command, item)"><el-button link>更多</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-if="item.locked || item.status === 'active'" command="protection" :disabled="!item.locked && item.effective_status === 'source_invalid'">{{ item.locked ? '解锁' : '确认并锁定' }}</el-dropdown-item><el-dropdown-item v-if="item.status === 'active'" command="replace">替换为新事实</el-dropdown-item><el-dropdown-item v-if="item.status !== 'forgotten'" command="forget">遗忘</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></div><el-tag v-if="item.locked" size="small" effect="plain">已确认 · 锁定</el-tag><p class="message-text memory-body">{{ item.text }}</p><div class="check-metadata"><span>录入 {{ formatTime(item.created_at, timezone) }}</span><span>v{{ item.revision }}</span><span v-if="item.valid_from != null">生效 {{ formatTime(item.valid_from, timezone) }}</span><span>{{ item.evidence_count }} 条来源</span><span v-if="item.expires_at">到期 {{ formatTime(item.expires_at, timezone) }}</span></div></article>
    <div v-if="next" class="load-more"><el-button :loading="loading" @click="load(true)">载入更多</el-button></div>
  </section>
  <MemoryProposals v-if="selected" :instance-id="selected" :timezone="timezone" :refresh-key="refreshKey" @changed="load()" />
  <EditDialog ref="memoryDialog" :draft="[form, expires, sourceText, validFrom]" :busy="saving" v-model="visible" :title="editing ? '编辑记忆' : form.supersedes.length ? '用新事实替代旧记忆' : '新增记忆'" width="680px" class="model-dialog" :close-on-click-modal="false" :show-close="!saving" :close-on-press-escape="!saving"><el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" /><el-form label-position="top" :disabled="saving" @submit.prevent="save"><el-form-item label="类型"><el-select v-model="form.kind"><el-option v-for="(label, kind) in kindLabels" :key="kind" :value="kind" :label="label" :disabled="kind === 'temporary'" /></el-select></el-form-item><el-form-item label="内容"><el-input v-model="form.text" type="textarea" :rows="6" maxlength="1000" /></el-form-item><el-form-item v-if="form.kind === 'temporary'" label="过期时间（本机时区）"><el-date-picker v-model="expires" type="datetime" value-format="x" @update:model-value="(value: string | number | null) => { expires = value ? Number(value) : null }" /></el-form-item><el-form-item v-if="editing" label="替换整组依据"><el-switch v-model="form.replace_sources" active-text="仅保留本次选择；历史版本保留" /></el-form-item><el-form-item :label="form.replace_sources ? '新的完整依据' : '补充来源'"><el-button @click="sourcePicker = true">选择输入／送达／生活事件</el-button><el-tag v-for="(source, index) in form.sources" :key="`${source.kind}-${source.id}`" closable @close="form.sources.splice(index, 1)">{{ sourceNames[source.kind] }} #{{ source.id }}</el-tag></el-form-item><el-collapse v-model="advancedPanels" class="quiet-collapse"><el-collapse-item title="事实时间与替代关系" name="replacement"><el-form-item label="事实生效时间（可选，本机时区）"><el-date-picker v-model="validFrom" type="datetime" value-format="x" @update:model-value="(value: string | number | null) => { validFrom = value == null ? null : Number(value) }" /></el-form-item><el-form-item label="替代旧记忆（可选）"><el-select v-model="form.supersedes" multiple value-key="memory_id" filterable :multiple-limit="4"><el-option v-for="row in replacementChoices.filter(row => row.id !== editing)" :key="row.id" :value="{ memory_id: row.id, revision: row.revision }" :label="`${row.locked ? '锁定 · ' : ''}${row.text.slice(0, 70)}`" /></el-select></el-form-item><p class="muted">旧事实保留追溯并退出召回；仅补充经历时无需替代。选项来自当前已加载的记忆。</p></el-collapse-item><el-collapse-item title="兼容：来源输入编号" name="sources"><el-input v-model="sourceText" placeholder="空格或逗号分隔；管理员可以不填，已有来源不会删除" /></el-collapse-item></el-collapse><div class="form-actions"><el-button @click="memoryDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!form.text.trim()">保存</el-button></div></el-form></EditDialog>
  <el-drawer v-model="detailVisible" title="来源与版本" size="min(820px, 94vw)" class="detail-drawer" destroy-on-close @closed="detailVersion++"><template v-if="detail"><p class="message-text">{{ detail.memory.text }}</p><p v-if="detail.memory.confirmed_at" class="muted">人工确认 {{ formatTime(detail.memory.confirmed_at, timezone) }}{{ detail.memory.locked ? ' · 已锁定' : ' · 当前未锁定' }}</p><div v-if="detail.replacement" class="check-record"><p>本条已被新事实替代，不再召回。</p><p class="message-text">{{ detail.replacement.text }}</p><el-button link @click="showDetail(detail.replacement)">查看新事实</el-button></div><details v-if="detail.replaces?.length" class="raw-details"><summary>被本条替代的旧事实</summary><article v-for="row in detail.replaces" :key="row.id"><p class="message-text">{{ row.text }}</p><el-button link @click="showDetail(row)">查看旧事实与版本</el-button></article></details><h3>来源记录（最多 100 条）</h3><el-empty v-if="!detail.sources.length" description="未关联来源" /><article v-for="source in detail.sources" :key="`${source.kind}-${source.id}`" class="check-record"><div class="check-metadata">{{ sourceNames[source.kind] }} #{{ source.id }} · v{{ source.revision }} · {{ formatTime(source.at, timezone) }}</div><el-tag v-if="source.validity?.invalid" type="warning">来源已失效：{{ source.validity.reason }}</el-tag><ConversationWindowLink :instance-id="detail.memory.instance_id" :source="source" :timezone="timezone" :disabled="!!source.validity?.invalid || source.deleted" /><p>{{ source.meaning }}</p><p class="message-text">{{ source.text }}</p><DataDetails v-if="source.state_changes" :value="source.state_changes" /></article><el-tag v-if="detail.sources_limited" type="info">仅展示前 100 条来源</el-tag><el-collapse><el-collapse-item v-for="item in detail.versions" :key="item.revision" :name="item.revision" :title="`v${item.revision} · ${item.run_id ? '模型运行 #' + item.run_id : '管理员录入'}`"><pre class="diagnostic-json">{{ item.config_json }}</pre></el-collapse-item></el-collapse></template><el-skeleton v-else :rows="5" animated /></el-drawer>
  <EditDialog v-model="protectionOpen" :title="protectionLocked ? '确认并锁定记忆' : '解锁记忆'" width="560px" :busy="protectionBusy" :draft="[protectionLocked, promote]">
    <el-alert v-if="protectionError" :title="protectionError" type="error" :closable="false" class="form-alert" />
    <p class="message-text">{{ protectionTarget?.text }}</p>
    <p class="muted">{{ protectionLocked ? '模型更改需你审核；来源失效、删除和原期限仍生效。' : '之后允许模型按原来源和版本规则更新，不会自动采用旧提案。' }}</p>
    <el-checkbox v-if="protectionLocked && protectionTarget?.kind !== 'profile'" v-model="promote" :disabled="protectionBusy">同时转为长期画像（移除临时期限）</el-checkbox>
    <div class="form-actions"><el-button :disabled="protectionBusy" @click="protectionOpen = false">取消</el-button><el-button type="primary" :loading="protectionBusy" @click="saveProtection">{{ protectionLocked ? '确认并锁定' : '解锁' }}</el-button></div>
  </EditDialog>
  <EvidencePicker v-model="sourcePicker" v-model:selection="form.sources" :instance-id="editorInstance" :timezone="timezone" />
</template>
