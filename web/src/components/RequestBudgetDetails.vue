<script setup lang="ts">
import { computed } from 'vue'
import type { RequestBudget } from '../material-types'
import PlanContractDetails from './PlanContractDetails.vue'
const props = defineProps<{ value: string | null | undefined; blocked?: boolean }>()
const report = computed(() => {
  try {
    const data = props.value ? JSON.parse(props.value) : null
    const value = props.blocked ? data?.blocked_step_budget : data?.context_budget
    return value && typeof value === 'object' && Number.isFinite(value.estimated_input_tokens) ? value as RequestBudget : null
  } catch { return null }
})
const labels = { coarse: '原始粗估', anchored: '历史参照校准', continuation: '同轮续接校准' }
const calibration = computed(() => report.value?.budget_calibration)
</script>
<template>
  <section v-if="report" class="request-budget">
    <div class="section-toolbar"><h3>{{ blocked ? '发送前预算拦截' : '本步请求预算' }}</h3><el-tag :type="report.over_budget ? 'danger' : 'info'" effect="plain">{{ labels[calibration?.method ?? 'coarse'] }}</el-tag></div>
    <dl class="detail-list overview-details"><div><dt>预算参考</dt><dd>{{ (report.budget_input_tokens ?? report.estimated_input_tokens).toLocaleString() }} tokens</dd></div><div><dt>原始粗估</dt><dd>{{ report.estimated_input_tokens.toLocaleString() }} tokens</dd></div><div><dt>可用输入</dt><dd>{{ report.available_input_tokens == null ? '未配置窗口' : report.available_input_tokens.toLocaleString() + ' tokens' }}</dd></div><div v-if="calibration?.anchor_step_id"><dt>参照步骤</dt><dd>#{{ calibration.anchor_step_id }} · 实际输入 {{ calibration.anchor_input_tokens?.toLocaleString() }}</dd></div><div v-if="calibration?.suffix_tokens != null"><dt>新增后缀粗估</dt><dd>{{ calibration.suffix_tokens.toLocaleString() }} tokens</dd></div><div v-if="calibration?.margin_tokens"><dt>校准额外余量</dt><dd>{{ calibration.margin_tokens.toLocaleString() }} tokens</dd></div></dl>
    <p v-if="calibration?.reason" class="muted">{{ calibration.reason }}</p>
    <PlanContractDetails :contract="report.plan_contract" />
  </section>
</template>
<style scoped>
.request-budget { margin: 18px 0 24px; }
.request-budget > p { line-height: 1.7; }
</style>
