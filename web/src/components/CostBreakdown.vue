<script setup lang="ts">
import { computed } from 'vue'
import type { CacheComparison, CostPart, CostParts } from '../usage-types'

const props = defineProps<{ parts?: CostParts | null; currency: string; comparison?: CacheComparison | null }>()
const labels: Record<CostPart, string> = { output: '输出（含推理）', cache_read: '命中缓存输入', uncached_input: '未命中输入', cache_write: '单独缓存写入', flat_input: '统一计价输入' }
const colors: Record<CostPart, string> = { output: '#456b69', cache_read: '#789a98', uncached_input: '#c4a470', cache_write: '#a5aa77', flat_input: '#8c9494' }
const entries = computed(() => (Object.keys(labels) as CostPart[]).flatMap(key => {
  const value = props.parts?.[key]
  return value == null ? [] : [{ key, value, amount: Number(value), label: labels[key], color: colors[key] }]
}))
const total = computed(() => entries.value.reduce((sum, item) => sum + item.amount, 0))
const segments = computed(() => entries.value.filter(item => item.amount > 0))
const extraCost = computed(() => Number(props.comparison?.difference) < 0)
const percent = (amount: number) => total.value > 0 ? `${(amount / total.value * 100).toFixed(1)}%` : '—'
function money(value: string | null | undefined, absolute = false) {
  if (value == null) return '未知'
  const amount = absolute ? Math.abs(Number(value)) : Number(value)
  if (amount > 0 && amount < .000001) return `${props.currency} <0.000001`
  return `${props.currency} ${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`
}
function hint(key: CostPart, value: string, amount: number) {
  const scope = key === 'uncached_input' ? '缓存写入另计时，此项不含单独写入；否则写入沿用未命中价格。' : ''
  return `${labels[key]}：${props.currency} ${value}；占已估响应费用 ${percent(amount)}。${scope}`
}
const comparisonHint = computed(() => {
  const value = props.comparison
  const scope = value?.records == null ? '仅本条可估响应' : `仅 ${value.records} 条可比较响应，另 ${value.excluded_records ?? 0} 条不参与`
  return `${scope}。固定同量输入，全部改按未命中单价计算，与原输入费用比较；输出不变，写入另计已纳入原输入费。不是关闭缓存或保活后的净收益预测，不补算缺失尝试。`
})
</script>

<template>
  <div class="cost-breakdown">
    <template v-if="entries.length">
      <div v-if="segments.length" class="cost-bar" role="group" aria-label="已估响应费用构成">
        <el-tooltip v-for="item in segments" :key="item.key" :content="hint(item.key, item.value, item.amount)">
          <span tabindex="0" :aria-label="hint(item.key, item.value, item.amount)" :style="{ width: `${item.amount / total * 100}%`, background: item.color }" />
        </el-tooltip>
      </div>
      <dl class="cost-components">
        <div v-for="item in entries" :key="item.key">
          <dt><i :style="{ background: item.color }" />{{ item.label }}</dt>
          <dd><el-tooltip :content="hint(item.key, item.value, item.amount)"><span tabindex="0">{{ money(item.value) }}</span></el-tooltip><span class="muted">{{ percent(item.amount) }}</span></dd>
        </div>
      </dl>
    </template>
    <p v-else class="muted">没有可拆分的响应费用</p>
    <div v-if="comparison?.difference != null" class="cost-comparison">
      <div class="comparison-heading"><strong>同量输入对照</strong><el-tooltip :content="comparisonHint"><span class="help-dot" tabindex="0" aria-label="查看同量输入对照口径">?</span></el-tooltip><span v-if="comparison.records != null" class="muted">{{ comparison.records }} 条可比较<span v-if="comparison.excluded_records"> · {{ comparison.excluded_records }} 条未参与</span></span></div>
      <dl class="comparison-values">
        <div><dt>已估输入费</dt><dd :title="`${currency} ${comparison.input_cost}`">{{ money(comparison.input_cost) }}</dd></div>
        <div><dt>全部未命中时</dt><dd :title="`${currency} ${comparison.uncached_input_cost}`">{{ money(comparison.uncached_input_cost) }}</dd></div>
        <div :class="{ 'extra-cost': extraCost }"><dt>{{ extraCost ? '比全未命中多支出' : '输入费用差额' }}</dt><dd :title="`${currency} ${comparison.difference}`">{{ money(comparison.difference, true) }}</dd></div>
      </dl>
    </div>
  </div>
</template>

<style scoped>
.cost-breakdown { display: grid; gap: 18px; margin: 18px 0 24px; }
.cost-bar { display: flex; height: 12px; overflow: hidden; border-radius: 999px; background: var(--rp-muted-surface); }
.cost-bar > span { flex: none; transition: filter .16s var(--rp-ease); }
.cost-bar > span:hover, .cost-bar > span:focus-visible { filter: brightness(1.12); outline: 2px solid var(--rp-text); outline-offset: -2px; }
.cost-components { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 16px 28px; margin: 0; }
.cost-components dt { display: flex; align-items: center; gap: 7px; color: var(--rp-muted); }
.cost-components i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.cost-components dd { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 0; font-variant-numeric: tabular-nums; }
.cost-components dd > :first-child { color: var(--rp-text); }
.cost-comparison { padding-top: 18px; border-top: 1px solid var(--rp-line); }
.comparison-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.comparison-heading .help-dot { margin-left: 0; }
.comparison-values { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); gap: 18px; margin: 16px 0 0; }
.comparison-values dt { color: var(--rp-muted); }
.comparison-values dd { margin: 7px 0 0; font-weight: 600; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.comparison-values .extra-cost dd { color: var(--el-color-warning-dark-2); }
@media (prefers-reduced-motion: reduce) { .cost-bar > span { transition: none; } }
</style>
