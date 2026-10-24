<script setup lang="ts">
import { computed } from 'vue'
import type { ContextReport } from '../material-types'
const props = defineProps<{ report: ContextReport }>()
const colors: Record<string, string> = { system: '#8c9494', preset: '#9e8eb7', world: '#a5aa77', character: '#c28e9b', profile: '#77a5b0', state: '#c4a470', retrieval: '#7a9db9', summary: '#d2aa68', history: '#709a84', input: '#a3b79e', runtime: '#aeb5ad', reserve: '#d9d2c7', safety: '#e1ddd5', free: 'var(--rp-muted-surface)' }
const denominator = computed(() => props.report.window ?? props.report.estimated_input_tokens + props.report.output_reserve + props.report.safety_margin)
const segments = computed(() => {
  const report = props.report
  const source = report.layers ?? [{ id: 'system', label: '输入合计', tokens: report.estimated_input_tokens }]
  return [...source, { id: 'reserve', label: '输出预留', tokens: report.output_reserve }, { id: 'safety', label: '安全余量', tokens: report.safety_margin }, { id: 'free', label: '空闲', tokens: Math.max(0, denominator.value - report.estimated_input_tokens - report.output_reserve - report.safety_margin) }].filter(item => item.tokens > 0)
})
const total = computed(() => segments.value.reduce((n, item) => n + item.tokens, 0))
const percent = (tokens: number) => `${(tokens / Math.max(1, denominator.value) * 100).toFixed(1)}%`
const number = (tokens: number) => tokens.toLocaleString()
const calibrated = computed(() => ['anchored', 'continuation'].includes(props.report.budget_calibration?.method ?? 'coarse'))
const budget = computed(() => props.report.budget_input_tokens ?? props.report.estimated_input_tokens)
const budgetHint = computed(() => {
  const value = props.report.budget_calibration
  return calibrated.value ? `${value?.method === 'continuation' ? '采用本轮刚返回的请求' : `参考 ${value?.samples} 次相近请求`}；采用步骤 ${value?.anchor_step_id} 的实际输入总量，加完整新后缀与 ${value?.margin_tokens?.toLocaleString()} Token余量。分项仍为原始粗估，不是实际计费分配。` : value?.reason || '未有足够相近记录，使用原始粗估。'
})
</script>
<template>
  <div class="context-occupancy">
    <div class="section-toolbar"><strong>{{ report.window ? `窗口 ${number(report.window)} tokens` : '尚未配置窗口 · 仅显示组成' }}</strong><div class="inline-actions"><span class="muted">原始粗估 {{ number(report.estimated_input_tokens) }}</span><el-tooltip :content="budgetHint"><el-tag :type="report.over_budget ? 'danger' : calibrated ? 'success' : 'info'" effect="plain">{{ calibrated ? '校准预算' : '预算参考' }} {{ number(budget) }}{{ report.window ? ` · ${percent(budget)}` : '' }}</el-tag></el-tooltip></div></div>
    <div class="context-capsule" role="group" aria-label="上下文组成；每段可聚焦查看估算">
      <el-tooltip v-for="item in segments" :key="item.id" :content="`${item.label} · 约 ${number(item.tokens)} tokens${report.window ? ` · ${percent(item.tokens)}` : ''}`" placement="top"><span tabindex="0" class="context-segment" :style="{ width: `${item.tokens / Math.max(1, total) * 100}%`, background: colors[item.id] }" :aria-label="`${item.label}，约${number(item.tokens)} tokens${report.window ? `，${percent(item.tokens)}` : ''}`" /></el-tooltip>
    </div>
    <p v-if="report.window && total > report.window" :class="report.over_budget ? 'check-error' : 'muted'">分项粗估含预留超出窗口 {{ number(total - report.window) }} tokens；条形按比例缩放{{ calibrated ? '，是否超限以校准预算及原预留判断' : '' }}。</p>
    <div class="context-legend"><span v-for="item in segments" :key="item.id"><i :style="{ background: colors[item.id] }" />{{ item.label }} <b>{{ report.window ? percent(item.tokens) : number(item.tokens) }}</b></span></div>
  </div>
</template>
<style scoped>
.context-occupancy { display: grid; gap: 14px; margin: 20px 0; }
.context-capsule { display: flex; height: 24px; border-radius: 999px; overflow: hidden; background: var(--rp-muted-surface); box-shadow: inset 0 0 0 1px var(--rp-line); }
.context-segment { display: block; flex: none; min-width: 0; transition: filter .16s var(--rp-ease); }
.context-segment:hover, .context-segment:focus-visible { filter: brightness(1.12); outline: 2px solid var(--rp-text); outline-offset: -2px; }
.context-legend { display: flex; flex-wrap: wrap; gap: 10px 18px; color: var(--rp-muted); }
.context-legend > span { display: inline-flex; align-items: center; gap: 6px; }
.context-legend i { width: 9px; height: 9px; border-radius: 3px; }
.context-legend b { font-weight: 500; color: var(--rp-text); }
@media (prefers-reduced-motion: reduce) { .context-segment { transition: none; } }
</style>
