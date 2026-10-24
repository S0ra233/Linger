<script setup lang="ts">
import { computed, onScopeDispose, ref, toRaw, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorText } from '../api'
import type { ModelEntry } from '../model-types'
import type { PresetCatalog, PresetContent, ProfileBinding, SavedPreset } from '../preset-types'
import PresetConfigEditor from './PresetConfigEditor.vue'
import SettingsSection from './SettingsSection.vue'

const props = defineProps<{ catalog: PresetCatalog | null }>()
const emit = defineEmits<{ busyChange: [value: boolean] }>()
const entry = defineModel<ModelEntry>({ required: true })
const fieldNames: Record<string, string> = { max_output_tokens: '最大输出', temperature: '温度', reasoning_effort: '期望强度', context_window: '上下文窗口', context_bytes: '输入字节上限', output_reserve: '输出预留', safety_margin: '安全余量', retain_turns: '近期轮数', role_prompt: '系统提示词', post_history_prompt: '历史后提示词', tool_submission_hint: '工具提交提醒', prompt_blocks: '独立提示词段', worldbook_budget_tokens: '世界书预算', memory_budget_tokens: '记忆预算', knowledge_budget_tokens: '资料预算', retrieval_mode: '检索方式', retrieval_top_k: '召回数量', auto_summary: '自动摘要', trigger_ratio: '摘要阈值', summary_target_tokens: '摘要目标', summary_max_tokens: '摘要上限', summary_timeout: '摘要时限', summary_instructions: '摘要提示词' }
Object.assign(fieldNames, { summary_max_batches: '每轮摘要批次上限', pending_input_limit: '单轮待处理输入上限' })
Object.assign(fieldNames, { strategy: '上下文模式', history_trigger_turns: '历史触发轮数', history_trigger_tokens: '历史Token触发量', history_target_tokens: '历史Token目标', retrieval_enabled: '旧自动检索字段（已停用）', retrieval_refresh_turns: '旧检索间隔（已停用）', allow_information_tools: '允许模型检索工具', retain_retrieval_snapshot: '保留检索快照', retrieval_budget_tokens: '检索总预算', retrieval_target_tokens: '检索缩减目标', trim_on_material_change: '资料变化时联动缩减', state_injection: '前置生活状态', state_refresh_turns: '生活状态刷新间隔' })
const localFields = computed(() => { const overrides = entry.value.profile?.overrides; return overrides ? [...Object.keys(overrides).filter(key => key !== 'context').map(key => ({ key, context: false })), ...Object.keys(overrides.context ?? {}).map(key => ({ key, context: true }))] : [] })
function resetField(key: string, inContext: boolean) { if (!draft.value || !base.value) return; if (inContext) Object.assign(draft.value.context, { [key]: clone(base.value.context[key as keyof PresetContent['context']]) }); else Object.assign(draft.value, { [key]: base.value[key as 'temperature' | 'max_output_tokens' | 'reasoning_effort'] }) }
const promptOpen = ref(false)
const base = ref<PresetContent | null>(null), error = ref(''), loading = ref(false), busy = ref(false), diffVisible = ref(false)
watch(busy, value => emit('busyChange', value), { flush: 'sync' })
onScopeDispose(() => emit('busyChange', false))
let version = 0
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const current = computed(() => props.catalog?.items.find(item => item.id === entry.value.profile?.preset_id))
function fieldValue(key: string, inContext: boolean, source: PresetContent | null) {
  if (!source) return '尚未载入'
  const value = inContext ? source.context[key as keyof PresetContent['context']] : source[key as 'temperature' | 'max_output_tokens' | 'reasoning_effort']
  return value === null ? '不发送／未配置' : typeof value === 'string' ? value || '空内容' : JSON.stringify(value, null, 2)
}
const selected = computed({ get: () => entry.value.profile?.preset_id ?? '', set: (id: string) => { entry.value.profile = { preset_id: id || null, revision: id ? props.catalog!.items.find(item => item.id === id)!.revision : null, overrides: clone(entry.value.profile?.overrides ?? {}) } } })
const effective = computed<PresetContent | null>(() => {
  if (!base.value) return null
  const value = { ...clone(base.value), ...clone(entry.value.profile?.overrides ?? {}), context: { ...clone(base.value.context), ...clone(entry.value.profile?.overrides.context ?? {}) } }
  // 旧模型本地覆盖可能尚无运行条件；只规范化编辑草稿，不改写保存中的旧版本。
  value.context.prompt_blocks = value.context.prompt_blocks.map(block => ({ ...block, run_types: block.run_types ?? [] }))
  return value
})
function update(value: PresetContent) {
  if (!base.value) return
  const overrides: ProfileBinding['overrides'] = {}, context: Partial<PresetContent['context']> = {}
  for (const key of Object.keys(value.context) as (keyof PresetContent['context'])[]) if (JSON.stringify(value.context[key]) !== JSON.stringify(base.value.context[key])) Object.assign(context, { [key]: clone(value.context[key]) })
  if (Object.keys(context).length) overrides.context = context
  if (value.max_output_tokens !== base.value.max_output_tokens) overrides.max_output_tokens = value.max_output_tokens
  if (value.temperature !== base.value.temperature) overrides.temperature = value.temperature
  if (value.reasoning_effort !== base.value.reasoning_effort) overrides.reasoning_effort = value.reasoning_effort
  entry.value.profile = { preset_id: entry.value.profile?.preset_id ?? null, revision: entry.value.profile?.revision ?? null, overrides }
  entry.value.max_output_tokens = value.max_output_tokens; entry.value.temperature = value.temperature
}
// 子编辑器会原地编辑嵌套字段；用独立草稿和深监听转换成相对预设的差异。
const draft = ref<PresetContent | null>(null)
let syncing = false
watch(effective, value => { syncing = true; draft.value = value ? clone(value) : null; queueMicrotask(() => { syncing = false }) }, { deep: true })
watch(draft, value => { if (!syncing && value) update(value) }, { deep: true, flush: 'sync' })
async function load() {
  if (!props.catalog) return
  const mine = ++version; loading.value = true; error.value = ''; base.value = null
  try {
    const binding = entry.value.profile
    if (binding?.preset_id) {
      const row = await api<SavedPreset>(`/models/presets/${binding.preset_id}?revision=${binding.revision}`)
      if (mine === version) base.value = row.config
    } else if (mine === version) {
      // 无预设的旧模型仍从旧默认值解析；新条目显式保存新模式差异。
      base.value = clone(props.catalog.legacy_defaults)
      if (!binding) {
        const overrides: ProfileBinding['overrides'] = {}
        if (entry.value.max_output_tokens !== base.value.max_output_tokens) overrides.max_output_tokens = entry.value.max_output_tokens
        if (entry.value.temperature !== base.value.temperature) overrides.temperature = entry.value.temperature
        if (!entry.value.id) {
          const context: Partial<PresetContent['context']> = {}
          for (const key of Object.keys(props.catalog.defaults.context) as (keyof PresetContent['context'])[]) {
            if (JSON.stringify(props.catalog.defaults.context[key]) !== JSON.stringify(base.value.context[key])) Object.assign(context, { [key]: clone(props.catalog.defaults.context[key]) })
          }
          overrides.context = context
        }
        entry.value.profile = { preset_id: null, revision: null, overrides }
      }
    }
    if (mine === version && !draft.value && effective.value) draft.value = clone(effective.value)
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function latest() { if (entry.value.profile && current.value) entry.value.profile.revision = current.value.revision }
async function saveAs() {
  if (!effective.value || busy.value) return
  let name: string
  try { name = (await ElMessageBox.prompt('预设名称', '另存为预设', { inputValue: entry.value.label || entry.value.model, confirmButtonText: '保存', cancelButtonText: '取消', inputValidator: (value: string) => !!value.trim() && value.length <= 80 || '名称须为 1—80 字符' })).value }
  catch { return }
  busy.value = true
  try { const value = await api<SavedPreset>('/models/presets', { method: 'POST', body: JSON.stringify({ name, config: toRaw(effective.value), revision: 0 }) }); entry.value.profile = { preset_id: value.id, revision: value.revision, overrides: {} }; props.catalog?.items.unshift(value); ElMessage.success('预设已保存；保存连接后生效') }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
watch([() => props.catalog, () => entry.value.profile?.preset_id ?? '', () => entry.value.profile?.revision ?? 0], load, { immediate: true })
</script>
<template>
  <div :inert="busy || undefined">
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert"><el-button link :disabled="loading" @click="load">重新载入预设</el-button></el-alert>
  <el-form-item label="预设"><el-select v-model="selected" :disabled="!catalog || loading"><el-option value="" label="自定义（内置配置为起点）" /><el-option v-for="item in catalog?.items ?? []" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
  <section class="profile-summary"><div><strong>{{ current?.name || (entry.profile?.preset_id ? '已绑定预设' : '自定义配置') }}</strong><span v-if="entry.profile?.preset_id">已绑定</span><span v-else>仅此模型使用</span><el-tag v-if="localFields.length" effect="plain" type="warning">{{ localFields.length }} 项本地修改</el-tag></div><div class="inline-actions"><el-button v-if="current && current.revision > (entry.profile?.revision ?? 0)" :disabled="loading" @click="latest">同步预设</el-button><el-button v-if="localFields.length" link @click="diffVisible = true">查看差异</el-button><el-button link :disabled="!effective || loading" :loading="busy" @click="saveAs">另存为预设</el-button></div></section>
  <el-alert v-if="current && current.revision > (entry.profile?.revision ?? 0)" title="仍保留原预设，可同步后保存连接。本模型的修改会保留。" type="info" :closable="false" class="form-alert" />
  <el-form-item v-if="draft" label="默认上下文窗口"><div class="inline-actions"><el-input-number v-model="draft.context.context_window" :disabled="loading" :min="2048" :max="10000000" :value-on-clear="null" controls-position="right" placeholder="按接口容量" /><el-tooltip content="填入 65536 tokens；联系人可单独覆盖，保存连接后生效。"><el-button :disabled="loading" @click="draft!.context.context_window = 65536">64K</el-button></el-tooltip></div></el-form-item>
  <SettingsSection v-if="draft" v-model="promptOpen" title="本模型提示词与参数" icon="document" :disabled="loading"><PresetConfigEditor v-model="draft" :templates="catalog?.defaults.context.prompt_blocks.filter(block => !block.enabled)" /></SettingsSection>
  </div>
  <el-drawer v-model="diffVisible" title="当前模型的本地修改" size="min(820px, 100vw)" class="detail-drawer"><p class="muted">与所绑定的预设比较；恢复继承后仍需保存连接。</p><el-empty v-if="!localFields.length" description="当前没有本地修改" /><section v-for="field in localFields" :key="`${field.context}-${field.key}`" class="check-record"><div class="section-toolbar"><h3>{{ fieldNames[field.key] || field.key }}</h3><el-button link :disabled="busy || loading" @click="resetField(field.key, field.context)">恢复继承</el-button></div><details class="diff-values"><summary>预设值</summary><pre class="diagnostic-json">{{ fieldValue(field.key, field.context, base) }}</pre></details><details open class="diff-values"><summary>本地值</summary><pre class="diagnostic-json">{{ fieldValue(field.key, field.context, draft) }}</pre></details></section><div v-if="localFields.length" class="form-actions"><el-button :disabled="busy || loading" @click="entry.profile!.overrides = {}">全部恢复继承</el-button></div></el-drawer>
</template>
