<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText } from '../api'
import type { StrategyBinding, StrategyCatalog, StrategyRules, ContextStrategy } from '../strategy-types'
import { strategyModes } from '../strategy-types'
import ContextRulesEditor from './ContextRulesEditor.vue'
import EditDialog from './EditDialog.vue'
const props = defineProps<{ instanceId: string; refreshKey: number }>()
const emit = defineEmits<{ saved: [] }>()
const binding = ref<StrategyBinding | null>(null), catalog = ref<StrategyCatalog | null>(null)
const error = ref(''), editError = ref(''), loading = ref(false), busy = ref(false), open = ref(false)
const editor = ref<InstanceType<typeof EditDialog> | null>(null)
const chosen = ref(''), draft = ref<StrategyRules | null>(null), base = ref<ContextStrategy | null>(null)
let version = 0, editingId = '', editingRevision = 0
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
interface WindowSettings { revision: number; overrides: Record<string, unknown>; base: StrategyRules; effective: StrategyRules; binding: StrategyBinding }
const rulesOpen = ref(false), rulesBusy = ref(false), rulesLoading = ref(false), rulesError = ref('')
const rulesDraft = ref<StrategyRules | null>(null), rulesSettings = ref<WindowSettings | null>(null)
const rulesDialog = ref<InstanceType<typeof EditDialog> | null>(null)
let rulesTarget = '', rulesVersion = 0
async function editRules() {
  if (rulesLoading.value || rulesBusy.value) return
  const target = props.instanceId, mine = ++rulesVersion
  rulesTarget = target; rulesError.value = ''; rulesDraft.value = null; rulesSettings.value = null; rulesLoading.value = true
  try {
    const value = await api<WindowSettings>(`/roleplay/context/instances/${target}/rules`)
    if (mine !== rulesVersion || target !== props.instanceId) return
    rulesSettings.value = value
    const fields = Object.keys(catalog.value!.defaults.cost) as (keyof StrategyRules)[]
    rulesDraft.value = Object.fromEntries(fields.map(key => [key, value.effective[key]])) as StrategyRules
    rulesOpen.value = true
  } catch (cause) { if (mine === rulesVersion) error.value = errorText(cause) }
  finally { if (mine === rulesVersion) rulesLoading.value = false }
}
async function saveRules() {
  const settings = rulesSettings.value, draft = rulesDraft.value, target = rulesTarget
  if (!settings || !draft || rulesBusy.value || target !== props.instanceId) return
  const overrides = clone(settings.overrides)
  for (const key of Object.keys(draft) as (keyof StrategyRules)[]) {
    if (JSON.stringify(draft[key]) === JSON.stringify(settings.base[key])) delete overrides[key]
    else overrides[key] = draft[key]
  }
  rulesBusy.value = true; rulesError.value = ''
  try {
    if (settings.binding.strategy_id) await api('/context-strategies/bindings', { method: 'PUT', body: JSON.stringify({ strategy_id: settings.binding.strategy_id, strategy_revision: settings.binding.strategy_revision, targets: [{ instance_id: target, revision: settings.binding.revision, overrides }] }) })
    else await api(`/roleplay/context/instances/${target}/rules`, { method: 'PUT', body: JSON.stringify({ revision: settings.revision, overrides }) })
    if (target === props.instanceId) { rulesOpen.value = false; emit('saved'); ElMessage.success('此对话的上下文规则已保存'); await load() }
  } catch (cause) { if (target === props.instanceId) rulesError.value = errorText(cause) }
  finally { rulesBusy.value = false }
}
const windowOpen = ref(false), windowLoading = ref(false), windowBusy = ref(false), windowError = ref('')
const windowDraft = ref<number | null>(null), windowSettings = ref<WindowSettings | null>(null)
const windowDialog = ref<InstanceType<typeof EditDialog> | null>(null)
let windowTarget = '', windowVersion = 0
async function editWindow() {
  if (windowBusy.value || windowLoading.value) return
  const target = props.instanceId, mine = ++windowVersion
  windowTarget = target; windowSettings.value = null; windowDraft.value = null; windowError.value = ''
  windowLoading.value = true
  try {
    const value = await api<WindowSettings>(`/roleplay/context/instances/${target}/rules`)
    if (mine === windowVersion && target === props.instanceId) { windowSettings.value = value; windowDraft.value = Object.hasOwn(value.overrides, 'context_window') ? value.overrides.context_window as number | null : null }
  } catch (cause) { if (mine === windowVersion) windowError.value = errorText(cause) }
  finally { if (mine === windowVersion) { windowLoading.value = false; windowOpen.value = true } }
}
async function saveWindow() {
  const settings = windowSettings.value, target = windowTarget
  if (!settings || windowBusy.value || windowLoading.value || target !== props.instanceId) return
  // 只替换当前对话的窗口；保留其余覆盖及已采用的策略版本。
  const overrides = { ...clone(settings.overrides), context_window: windowDraft.value ?? null }
  if (windowDraft.value == null) delete (overrides as Record<string, unknown>).context_window
  windowBusy.value = true; windowError.value = ''
  try {
    if (settings.binding.strategy_id) await api('/context-strategies/bindings', { method: 'PUT', body: JSON.stringify({ strategy_id: settings.binding.strategy_id, strategy_revision: settings.binding.strategy_revision, targets: [{ instance_id: target, revision: settings.binding.revision, overrides }] }) })
    else await api(`/roleplay/context/instances/${target}/rules`, { method: 'PUT', body: JSON.stringify({ revision: settings.revision, overrides }) })
    if (target === props.instanceId) { windowOpen.value = false; ElMessage.success('上下文窗口已保存，下次运行采用'); emit('saved'); await load() }
  } catch (cause) { if (target === props.instanceId) windowError.value = errorText(cause) }
  finally { windowBusy.value = false }
}
async function load() {
  if (!props.instanceId || open.value || rulesOpen.value || windowOpen.value) return
  const mine = ++version
  loading.value = true; error.value = ''
  try { const [value, options] = await Promise.all([api<StrategyBinding>(`/context-strategies/bindings/${props.instanceId}`), api<StrategyCatalog>('/context-strategies')]); if (mine === version) { binding.value = value; catalog.value = options } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function choose(id: string) {
  chosen.value = id
  base.value = catalog.value?.items.find(item => item.id === id) ?? null
  draft.value = base.value ? clone(base.value.config) : null
}
function edit() {
  if (!binding.value || !catalog.value) return
  editingId = props.instanceId; editingRevision = binding.value.revision; editError.value = ''
  chosen.value = binding.value.strategy_id ?? ''; base.value = binding.value.strategy ? clone(binding.value.strategy) : null
  draft.value = base.value ? { ...clone(base.value.config), ...clone(binding.value.overrides) } : null
  open.value = true
}
async function save() {
  if (busy.value) return
  const overrides: Partial<StrategyRules> = {}
  if (draft.value && base.value) for (const key of Object.keys(draft.value) as (keyof StrategyRules)[]) if (JSON.stringify(draft.value[key]) !== JSON.stringify(base.value.config[key])) Object.assign(overrides, { [key]: draft.value[key] })
  busy.value = true; editError.value = ''
  try {
    await api('/context-strategies/bindings', { method: 'PUT', body: JSON.stringify({ strategy_id: base.value?.id ?? null, strategy_revision: base.value?.revision ?? null, targets: [{ instance_id: editingId, revision: editingRevision, overrides }] }) })
    open.value = false; ElMessage.success('对话策略已保存，下次运行采用'); emit('saved')
  } catch (cause) { editError.value = errorText(cause) }
  finally { busy.value = false }
}
watch(() => props.instanceId, () => { version++; windowVersion++; rulesVersion++; rulesOpen.value = false; rulesLoading.value = false; rulesDraft.value = null; open.value = false; windowOpen.value = false; windowLoading.value = false; windowSettings.value = null; loading.value = false; binding.value = null; void load() }, { immediate: true })
watch(() => props.refreshKey, load)
watch(open, value => { if (!value) void load() })
watch(windowOpen, value => { if (!value) { windowVersion++; windowLoading.value = false } })
watch(rulesOpen, value => { if (!value) void load() })
onScopeDispose(() => { version++; windowVersion++; rulesVersion++ })
</script>
<template>
  <section class="panel section-gap" v-loading="loading"><div class="section-toolbar"><h2>此对话的上下文</h2><div class="inline-actions"><el-button type="primary" :loading="rulesLoading" :disabled="!binding || !catalog || loading || windowBusy" @click="editRules">编辑规则</el-button><el-button :loading="windowLoading" :disabled="!binding || loading || rulesBusy" @click="editWindow">上下文窗口</el-button><el-button :disabled="!binding || loading || windowBusy || windowLoading || rulesBusy" @click="edit">{{ binding?.strategy ? '调整绑定' : '绑定策略' }}</el-button><a href="#strategies" class="action-link">策略库</a></div></div><el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="binding"><div v-if="binding.strategy" class="strategy-binding-summary"><strong>{{ binding.strategy.name }}</strong><el-tag effect="plain">{{ strategyModes[binding.strategy.config.strategy] }}</el-tag><el-tag v-if="Object.keys(binding.overrides).length" type="warning" effect="plain">此对话有单独调整</el-tag></div><p v-else class="muted">尚未绑定独立策略，暂沿用已有兼容配置。</p></template>
  </section>
  <EditDialog ref="windowDialog" v-model="windowOpen" title="上下文窗口" width="520px" :draft="windowDraft" :busy="windowBusy">
    <el-alert v-if="windowError" :title="windowError" type="error" :closable="false" class="form-alert" />
    <el-skeleton v-if="windowLoading" :rows="2" animated />
    <el-form v-else-if="windowSettings" label-position="top" :disabled="windowBusy" @submit.prevent="saveWindow">
      <el-form-item label="上下文窗口"><el-input-number v-model="windowDraft" :min="2048" :max="10000000" :value-on-clear="null" controls-position="right" placeholder="继承默认值" /></el-form-item>
      <div class="inline-actions"><el-button v-for="size in [16, 32, 64]" :key="size" :type="windowDraft === size * 1024 ? 'primary' : 'default'" plain @click="windowDraft = size * 1024">{{ size }}K</el-button><el-button link @click="windowDraft = null">恢复继承</el-button></div>
      <p class="field-hint">继承值：{{ windowSettings.base.context_window?.toLocaleString() ?? '按模型容量' }} tokens。</p>
      <p class="field-hint">仅此对话生效；实际容量仍受模型和本地引擎上限限制。</p>
      <div class="form-actions"><el-button @click="windowDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="windowBusy">保存窗口</el-button></div>
    </el-form>
    <div v-else class="form-actions"><el-button @click="windowDialog?.close()">关闭</el-button></div>
  </EditDialog>
  <EditDialog ref="rulesDialog" v-model="rulesOpen" title="此对话的上下文规则" width="min(900px, 94vw)" :draft="rulesDraft" :busy="rulesBusy">
    <el-alert v-if="rulesError" :title="rulesError" type="error" :closable="false" class="form-alert" />
    <el-form v-if="rulesDraft" label-position="top" :disabled="rulesBusy" @submit.prevent="saveRules"><ContextRulesEditor v-model="rulesDraft" /><div class="form-actions"><el-button @click="rulesDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="rulesBusy">保存此对话规则</el-button></div></el-form>
  </EditDialog>
  <EditDialog ref="editor" v-model="open" title="绑定对话策略" width="min(900px, 94vw)" :draft="{ chosen, draft }" :busy="busy">
    <el-alert v-if="editError" :title="editError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="busy" @submit.prevent="save"><el-form-item label="采用策略"><el-select :model-value="chosen" @update:model-value="choose"><el-option value="" label="不绑定（使用原兼容配置）" /><el-option v-for="item in catalog?.items ?? []" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
      <div v-if="base" class="section-toolbar"><span>已绑定策略</span><el-button v-if="(catalog?.items.find(item => item.id === chosen)?.revision ?? 0) > base.revision" link @click="choose(chosen)">同步策略并重置单独调整</el-button><el-button link @click="draft = clone(base!.config)">恢复策略原值</el-button></div>
      <ContextRulesEditor v-if="draft" v-model="draft" /><el-empty v-else-if="!catalog?.items.length" description="先在策略库新建规则，无需接入API" />
      <div class="form-actions"><el-button @click="editor?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="busy">保存绑定</el-button></div>
    </el-form>
  </EditDialog>
</template>
