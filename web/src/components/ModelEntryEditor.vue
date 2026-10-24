<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import type { ModelEntry, ReasoningCatalog } from '../model-types'
import type { PresetCatalog } from '../preset-types'
import ModelProfileEditor from './ModelProfileEditor.vue'
import ToolPolicyEditor from './ToolPolicyEditor.vue'
import SettingsSection from './SettingsSection.vue'
import NavIcon from './NavIcon.vue'

const props = defineProps<{ protocol: string; providerKind?: string; removable: boolean; presetCatalog: PresetCatalog | null; view?: 'basic' | 'profile' }>()
const entry = defineModel<ModelEntry>({ required: true })
const catalog = ref<ReasoningCatalog | null>(null), capabilityError = ref('')
const capabilitiesOpen = ref(true)
const localTab = ref('basic')
const selectedTab = computed({ get: () => props.view ?? localTab.value, set: (value: string | number) => { localTab.value = String(value) } })
onMounted(async () => { try { catalog.value = await api<ReasoningCatalog>('/models/capabilities') } catch (cause) { capabilityError.value = errorText(cause) } })
const emit = defineEmits<{ remove: []; busyChange: [value: boolean]; openProfile: [] }>()
const presetName = computed(() => {
  const id = entry.value.profile?.preset_id
  return id ? props.presetCatalog?.items.find(item => item.id === id)?.name ?? '已绑定预设' : '自定义配置'
})
function openProfile() { if (props.view) emit('openProfile'); else localTab.value = 'profile' }
const embedding = computed(() => props.protocol.startsWith('embedding_'))
const imageChoice = computed({ get: () => String(entry.value.image_input ?? 'unknown'), set: value => { entry.value.image_input = value === 'unknown' ? null : value === 'true' } })
const toolChoice = computed({ get: () => String(entry.value.tool_input ?? 'unknown'), set: value => { entry.value.tool_input = value === 'unknown' ? null : value === 'true' } })
const thinkingChoice = computed({ get: () => String(entry.value.reasoning?.default_thinking ?? 'unknown'), set: value => { if (entry.value.reasoning) entry.value.reasoning.default_thinking = value === 'unknown' ? null : value === 'true' } })
const effortChoice = computed({ get: () => entry.value.reasoning_effort ?? 'unset', set: value => { entry.value.reasoning_effort = value === 'unset' ? null : value } })
const adapter = computed({ get: () => entry.value.reasoning?.adapter ?? '', set(value: string) {
  entry.value.reasoning = value ? { adapter: value, effort_map: {}, tool_efforts: null, default_thinking: null,
    temperature_efforts: null, image_efforts: null, min_output_tokens: null, max_output_tokens: null, max_thinking_tokens: null, thinking_mode: value === 'anthropic_combined' ? 'adaptive' : null, thinking_budget: null, effort_delivery: 'top_level', baseline_effort: null } : null
  entry.value.reasoning_effort = null
} })
const adapters = computed(() => (catalog.value?.adapters ?? []).filter(item => item.protocols.includes(props.protocol)).map(item => ({ value: item.id, label: item.label })))
const effortLevels = computed(() => catalog.value?.efforts ?? Object.keys(entry.value.reasoning?.effort_map ?? {}))
const budget = computed(() => adapter.value.endsWith('_budget'))
const nativeLevels = computed(() => catalog.value?.adapters.find(item => item.id === adapter.value)?.native_levels ?? [])
watch(() => entry.value.reasoning?.thinking_mode, value => { if (entry.value.reasoning && value !== 'enabled') entry.value.reasoning.thinking_budget = null })
watch(() => entry.value.reasoning?.effort_delivery, value => { if (entry.value.reasoning && value !== 'message') entry.value.reasoning.baseline_effort = null })
function addEffort() {
  const profile = entry.value.reasoning
  if (!profile) return
  const level = ['medium', 'low', 'high', 'none', 'minimal', 'xhigh', 'max', 'ultra'].find(item => !(item in profile.effort_map))
  if (level) profile.effort_map[level] = budget.value ? 2048 : nativeLevels.value.includes(level) ? level : nativeLevels.value[0]!
}
function renameEffort(old: string, value: string) {
  const profile = entry.value.reasoning
  if (!profile || value in profile.effort_map) return
  profile.effort_map[value] = profile.effort_map[old]!
  delete profile.effort_map[old]
  if (entry.value.reasoning_effort === old) entry.value.reasoning_effort = value
  if (profile.tool_efforts) profile.tool_efforts = profile.tool_efforts.map(item => item === old ? value : item)
  for (const key of ['temperature_efforts', 'image_efforts'] as const) if (profile[key]) profile[key] = profile[key]!.map(item => item === old ? value : item)
}
function removeEffort(level: string) {
  if (!entry.value.reasoning) return
  delete entry.value.reasoning.effort_map[level]
  if (entry.value.reasoning_effort === level) entry.value.reasoning_effort = null
  if (entry.value.reasoning.tool_efforts) entry.value.reasoning.tool_efforts = entry.value.reasoning.tool_efforts.filter(item => item !== level)
  for (const key of ['temperature_efforts', 'image_efforts'] as const) if (entry.value.reasoning[key]) entry.value.reasoning[key] = entry.value.reasoning[key]!.filter(item => item !== level)
}
</script>

<template>
  <section class="model-entry-editor">
    <el-alert v-if="capabilityError" :title="capabilityError" type="error" :closable="false" />
    <el-tabs v-model="selectedTab" :class="['editor-tabs', { 'externally-navigated-tabs': view }]">
    <el-tab-pane label="基本与能力" name="basic">
    <div class="model-identity-card"><span class="model-identity-icon" aria-hidden="true"><NavIcon name="models" /></span><div class="entry-main-row"><el-form-item label="API 模型 ID"><el-input v-model="entry.model" placeholder="服务商的 model 名称" maxlength="200" /></el-form-item><el-form-item label="显示名称"><el-input v-model="entry.label" placeholder="可选" maxlength="80" /></el-form-item><el-button v-if="removable" link type="danger" @click="$emit('remove')">移除</el-button></div></div>
    <SettingsSection v-model="capabilitiesOpen" title="能力与参数" icon="settings">
      <div v-if="embedding" class="form-two-columns"><el-form-item label="向量维度"><el-input-number v-model="entry.embedding_dimensions" :min="1" :max="16384" :value-on-clear="null" controls-position="right" placeholder="接口默认" /></el-form-item></div>
      <template v-else><div class="form-two-columns">
        <el-form-item label="图片输入"><el-select v-model="imageChoice"><el-option label="未知" value="unknown" /><el-option label="支持" value="true" /><el-option label="不支持" value="false" /></el-select></el-form-item>
        <el-form-item label="工具调用"><el-select v-model="toolChoice"><el-option label="未知" value="unknown" /><el-option label="支持" value="true" /><el-option label="不支持" value="false" /></el-select></el-form-item>
        <el-form-item label="接口最大上下文容量"><el-input-number v-model="entry.context_window" :min="1" :max="20000000" :value-on-clear="null" controls-position="right" placeholder="未知" /></el-form-item>
        <el-form-item label="思考参数适配"><el-select v-model="adapter"><el-option label="未知 / 不发送" value="" /><el-option v-for="item in adapters" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      </div>
      <div v-if="entry.reasoning && adapter !== 'unsupported'" class="reasoning-editor">
        <div class="section-toolbar"><h3>已确认的推理档位</h3><el-button size="small" :disabled="Object.keys(entry.reasoning.effort_map).length >= 8" @click="addEffort">添加档位</el-button></div>
        <div v-for="level in Object.keys(entry.reasoning.effort_map)" :key="level" class="effort-row">
          <el-select :model-value="level" aria-label="应用档位" @update:model-value="(value: string) => renameEffort(level, value)"><el-option v-for="choice in effortLevels" :key="choice" :value="choice" :label="choice" :disabled="choice !== level && choice in entry.reasoning.effort_map" /></el-select><span>→</span>
          <el-input-number v-if="budget" :model-value="Number(entry.reasoning.effort_map[level])" :min="adapter === 'gemini_budget' ? -1 : 0" controls-position="right" aria-label="原生 Token 预算" @update:model-value="(value: number | null | undefined) => { if (entry.reasoning && value != null) entry.reasoning.effort_map[level] = value }" />
          <el-select v-else v-model="entry.reasoning.effort_map[level]" aria-label="原生档位"><el-option v-for="native in nativeLevels" :key="native" :label="native" :value="native" /></el-select>
          <el-button link type="danger" @click="removeEffort(level)">移除</el-button>
        </div>
        <div class="form-two-columns"><el-form-item label="默认推理档位"><el-select v-model="effortChoice"><el-option label="不发送" value="unset" /><el-option v-for="level in Object.keys(entry.reasoning.effort_map)" :key="level" :value="level" :label="level" /></el-select></el-form-item>
          <el-form-item label="默认思考状态"><el-select v-model="thinkingChoice"><el-option label="未知" value="unknown" /><el-option label="默认开启" value="true" /><el-option label="默认关闭" value="false" /></el-select></el-form-item></div>
        <el-checkbox :model-value="entry.reasoning.tool_efforts !== null" @update:model-value="(value: boolean | string | number) => { if (entry.reasoning) entry.reasoning.tool_efforts = value ? [] : null }">限制工具可用档位</el-checkbox>
        <el-select v-if="entry.reasoning.tool_efforts !== null" v-model="entry.reasoning.tool_efforts" multiple placeholder="空列表表示禁止该组合"><el-option v-for="level in Object.keys(entry.reasoning.effort_map)" :key="level" :label="level" :value="level" /></el-select>
      </div>
      </template>
    </SettingsSection>
    <ToolPolicyEditor v-if="!embedding && toolChoice === 'true'" v-model="entry.tool_policy" :protocol="protocol" :provider-kind="providerKind" />
      <SettingsSection v-if="!embedding && entry.reasoning" title="参数组合限制" icon="check">
        <template v-if="entry.reasoning.adapter === 'anthropic_combined'"><el-form-item label="思考模式"><el-select v-model="entry.reasoning.thinking_mode"><el-option v-for="mode in ['enabled','disabled','adaptive','between_tools']" :key="mode" :value="mode" :label="mode" /></el-select></el-form-item><el-form-item v-if="entry.reasoning.thinking_mode === 'enabled'" label="思考 Token 预算"><el-input-number v-model="entry.reasoning.thinking_budget" :min="1024" :max="1000000" /></el-form-item></template>
        <template v-if="['anthropic','anthropic_adaptive','anthropic_combined'].includes(entry.reasoning.adapter)"><el-form-item label="effort 位置"><el-select v-model="entry.reasoning.effort_delivery"><el-option value="top_level" label="请求顶层" /><el-option value="message" label="逐消息（需端点支持 Beta）" /></el-select></el-form-item><el-form-item v-if="entry.reasoning.effort_delivery === 'message'" label="固定顶层基准"><el-select v-model="entry.reasoning.baseline_effort"><el-option v-for="effort in ['low','medium','high','xhigh','max']" :key="effort" :label="effort" :value="effort" /></el-select></el-form-item></template>
        <div v-for="item in [{ key: 'temperature_efforts' as const, label: '温度可用档位' }, { key: 'image_efforts' as const, label: '图片可用档位' }]" :key="item.key" class="form-field">
          <el-checkbox :model-value="entry.reasoning[item.key] !== null" @update:model-value="(value: boolean | string | number) => { if (entry.reasoning) entry.reasoning[item.key] = value ? [] : null }">{{ item.label }}</el-checkbox>
          <el-select v-if="entry.reasoning[item.key] !== null" v-model="entry.reasoning[item.key]" multiple placeholder="空列表表示禁止"><el-option v-for="level in Object.keys(entry.reasoning.effort_map)" :key="level" :value="level" :label="level" /></el-select>
        </div>
        <div class="form-two-columns"><el-form-item label="最小输出上限"><el-input-number v-model="entry.reasoning.min_output_tokens" :min="1" :max="1000000" :value-on-clear="null" controls-position="right" /></el-form-item><el-form-item label="最大输出上限"><el-input-number v-model="entry.reasoning.max_output_tokens" :min="1" :max="1000000" :value-on-clear="null" controls-position="right" /></el-form-item><el-form-item v-if="budget" label="最大思考预算"><el-input-number v-model="entry.reasoning.max_thinking_tokens" :min="1" :max="1000000" :value-on-clear="null" controls-position="right" /></el-form-item></div>
      </SettingsSection>
    <div v-if="!embedding" class="model-preset-card"><span class="model-identity-icon" aria-hidden="true"><NavIcon name="document" /></span><div><span class="muted">当前预设</span><strong>{{ presetName }}</strong></div><el-button @click="openProfile">预设设置</el-button></div>
    </el-tab-pane>
    <el-tab-pane v-if="!embedding" label="提示词预设" name="profile"><ModelProfileEditor v-model="entry" :catalog="presetCatalog" @busy-change="value => $emit('busyChange', value)" /></el-tab-pane>
    </el-tabs>
  </section>
</template>
