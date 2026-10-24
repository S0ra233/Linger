<script setup lang="ts">
import { computed } from 'vue'
import type { PlanContract } from '../material-types'
const props = defineProps<{ contract?: PlanContract | null }>()
const names: Record<string, string> = { schedules: '日程', events: '世界事件', observations: '公开观察' }
const capabilities = computed(() => ['核心回复与计划', ...(props.contract?.enabled_extensions ?? []).map(name => names[name] ?? name),
  ...(props.contract?.tool_names.includes('rp_search_information') ? ['资料检索'] : []),
  ...(props.contract?.tool_names.some(name => name.startsWith('mcp_')) ? ['MCP'] : [])])
</script>
<template>
  <div v-if="contract" class="plan-contract">
    <div class="section-toolbar"><strong>{{ contract.output === 'json' ? 'JSON计划与查询能力' : '本轮工具能力' }}</strong><span v-if="contract.omitted_fields.length" class="muted">已精简 {{ contract.omitted_fields.length }} 个扩展字段</span></div>
    <div class="inline-actions"><el-tag v-for="name in capabilities" :key="name" effect="plain" type="info">{{ name }}</el-tag></div>
    <details class="raw-details"><summary>计划字段与工具声明</summary><dl class="detail-list"><div><dt>计划字段</dt><dd class="break-anywhere">{{ contract.fields.join('、') }}</dd></div><div v-if="contract.omitted_fields.length"><dt>未启用字段</dt><dd>{{ contract.omitted_fields.join('、') }}</dd></div><div><dt>工具</dt><dd class="break-anywhere">{{ contract.tool_names.join('、') }}</dd></div><div><dt>计划结构指纹</dt><dd class="mono break-anywhere">{{ contract.schema_hash }}</dd></div></dl></details>
  </div>
</template>
<style scoped>
.plan-contract { display: grid; gap: 12px; margin: 18px 0; }
.plan-contract .section-toolbar { margin: 0; }
</style>
