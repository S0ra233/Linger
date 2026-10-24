<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ value: string | null }>()
interface Policy { requested: string; effective: string; schema_mode: string; parallel_mode: string; reason: string | null; plan_output?: 'json' | 'tool'; json_format?: 'json_object' | 'json_schema'; native?: Record<string, unknown>; wire?: Record<string, unknown> }
const policy = computed<Policy | null>(() => {
  if (!props.value) return null
  try { return JSON.parse(props.value).tool_policy ?? null } catch { return null }
})
const labels: Record<string, string> = { system: '系统策略', auto: '自主选择', required: '必须调用', prompt: '兼容引导', named: '指定提交工具', allowed: '仅允许提交工具' }
const wire = computed(() => policy.value?.wire ? Object.fromEntries(Object.entries(policy.value.wire).filter(([key]) =>
  ['tool_choice', 'response_format', 'text', 'thinking', 'output_config', 'generationConfig', 'toolConfig', 'reasoning', 'reasoning_effort'].includes(key))) : null)
</script>
<template>
  <dl v-if="policy" class="detail-list overview-details">
    <div v-if="policy.plan_output"><dt>计划输出</dt><dd>{{ policy.plan_output === 'json' ? (policy.json_format === 'json_schema' ? 'JSON Schema' : 'JSON计划') : '原生工具提交' }}</dd></div>
    <div><dt>{{ policy.plan_output === 'json' ? '查询工具' : '工具策略' }}</dt><dd>{{ labels[policy.requested] ?? policy.requested }} → {{ labels[policy.effective] ?? policy.effective }}</dd></div>
    <div><dt>参数约束</dt><dd>{{ policy.schema_mode === 'strict' ? '严格' : '兼容' }} · {{ policy.parallel_mode === 'serial' ? '单次单工具' : '接口默认数量' }}</dd></div>
    <div v-if="policy.reason"><dt>采用原因</dt><dd>{{ policy.reason }}</dd></div>
    <div v-if="policy.native && Object.keys(policy.native).length"><dt>接口约束</dt><dd class="break-anywhere mono">{{ JSON.stringify(policy.native) }}</dd></div>
    <div v-if="wire"><dt>实际请求字段</dt><dd class="break-anywhere mono">{{ JSON.stringify(wire) }}</dd></div>
  </dl>
</template>
