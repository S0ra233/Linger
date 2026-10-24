<script setup lang="ts">
import { computed } from 'vue'
import type { ToolPolicySettings } from '../model-types'
import SettingsSection from './SettingsSection.vue'
const props = defineProps<{ protocol: string; providerKind?: string }>()
const value = defineModel<ToolPolicySettings | null>({ required: true })
const defaults = (): ToolPolicySettings => ({ plan_output: 'system', normal_mode: 'system', repair_mode: 'system',
  force_with_thinking: false, schema_mode: 'compatible', parallel_mode: 'default' })
const enabled = computed({ get: () => !!value.value, set: active => {
  value.value = active ? defaults() : null
} })
const planOutput = computed({ get: () => value.value?.plan_output ?? 'system',
  set: (selected: 'system' | 'tool' | 'json') => { value.value = { ...defaults(), ...value.value, plan_output: selected } } })
const chat = computed(() => ['chat_completions', 'responses'].includes(props.protocol))
const deepseekResponses = computed(() => props.protocol === 'responses' && props.providerKind === 'deepseek')
const jsonSupported = computed(() => !(props.protocol === 'anthropic_messages' && props.providerKind === 'deepseek'))
const jsonPlan = computed(() => planOutput.value === 'json'
  || (planOutput.value === 'system' && chat.value && props.providerKind === 'deepseek'))
const strict = computed(() => props.protocol !== 'anthropic_messages' && !deepseekResponses.value)
const systemHint = computed(() => {
  if (jsonPlan.value) return `${chat.value && props.providerKind !== 'openai' ? 'JSON对象' : 'JSON Schema'}计划；查询工具与思考保持，完整校验后投递。`
  if (!jsonSupported.value) return 'DeepSeek Messages不支持原生JSON格式；JSON计划请使用Chat或Responses。'
  if ((props.providerKind === 'openai' && ['chat_completions', 'responses'].includes(props.protocol))
    || (props.providerKind === 'google' && props.protocol === 'gemini')) return '系统默认：原生必须调用工具，补交时指定计划工具。'
  return '系统默认：仅在已确认的协议组合启用原生必调；其他组合保留兼容引导并记录原因。'
})
</script>

<template>
  <SettingsSection title="工具策略" icon="integrations">
    <el-form-item label="计划输出"><el-select v-model="planOutput"><el-option label="系统默认" value="system" /><el-option label="JSON计划" value="json" :disabled="!jsonSupported" /><el-option label="原生工具提交" value="tool" /></el-select></el-form-item>
    <el-form-item label="自定义策略"><el-switch v-model="enabled" /></el-form-item>
    <p v-if="!value || jsonPlan || !jsonSupported" class="field-hint">{{ systemHint }}</p>
    <div v-if="value" class="form-two-columns">
      <el-form-item v-if="!jsonPlan" label="正常调用"><el-select v-model="value.normal_mode"><el-option label="系统策略" value="system" /><el-option label="自主选择" value="auto" /><el-option label="必须调用工具（已确认支持）" value="required" /></el-select></el-form-item>
      <el-form-item v-if="!jsonPlan" label="补交与参数修复"><el-select v-model="value.repair_mode"><el-option label="系统策略" value="system" /><el-option label="兼容引导" value="prompt" /><el-option label="指定提交工具" value="named" /><el-option v-if="['responses', 'gemini'].includes(protocol) && !deepseekResponses" label="仅允许提交工具" value="allowed" /></el-select></el-form-item>
      <el-form-item :label="jsonPlan ? '查询参数约束' : '参数结构约束'"><el-tooltip :content="jsonPlan ? '此处只约束原生查询工具参数；最终JSON计划仍由系统校验。' : '仅在接口明确支持时启用。Chat／Responses使用严格Schema投影；Gemini使用原生校验模式。'"><el-select v-model="value.schema_mode"><el-option label="兼容模式" value="compatible" /><el-option v-if="strict" label="严格模式（已确认支持）" value="strict" /></el-select></el-tooltip></el-form-item>
      <el-form-item v-if="chat && (!deepseekResponses || value.parallel_mode !== 'default')" label="单次调用数量"><el-select v-model="value.parallel_mode"><el-option label="接口默认" value="default" /><el-option v-if="!deepseekResponses" label="每次最多一个工具" value="serial" /></el-select></el-form-item>
      <el-form-item v-if="!jsonPlan" label="兼容端点已确认可强制工具"><el-tooltip content="用于未内置确认的兼容端点。已知会拒绝或跳过思考的组合仍保留兼容引导；不自动关闭思考。"><el-switch v-model="value.force_with_thinking" /></el-tooltip></el-form-item>
    </div>
  </SettingsSection>
</template>
