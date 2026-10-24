<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
interface CacheRatio { requests: number; paired_requests: number; missing_requests: number; ratio: number | null; input_tokens: number | null; cache_read_tokens: number | null; latest_at: number | null; unreported_attempts: number; untracked_requests: number; limit: number; model: string | null; status: string }
const props = defineProps<{ instanceId: string; refreshKey: number; timezone: string }>()
const result = ref<CacheRatio | null>(null), error = ref(''), loading = ref(false)
let version = 0
const percent = computed(() => result.value?.ratio == null ? null : result.value.ratio * 100)
async function load() {
  const mine = ++version, id = props.instanceId
  result.value = null; error.value = ''
  if (!id) { loading.value = false; return }
  loading.value = true
  try { const value = await api<CacheRatio>(`/usage/cache/instances/${id}?limit=20`); if (mine === version) result.value = value }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
watch([() => props.instanceId, () => props.refreshKey], load, { immediate: true })
</script>
<template>
  <section class="recent-cache"><div class="section-toolbar"><h3>近期输入缓存命中</h3><el-button link :loading="loading" :disabled="!instanceId" @click="load">刷新</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="result"><div class="cache-metric-row"><strong>{{ percent == null ? '暂无已报告比例' : percent.toFixed(1) + '%' }}</strong><span class="muted">{{ result.status === 'no_model' ? '尚未分配主叙事模型' : `当前模型 · 最近 ${result.requests} 次对话请求` }}</span></div><el-tooltip v-if="percent != null" :content="`有效配对输入 ${result.input_tokens?.toLocaleString()} tokens，命中 ${result.cache_read_tokens?.toLocaleString()} tokens`"><div class="cache-ratio-track" role="meter" aria-label="已报告输入缓存命中比例" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${percent}%` }" /></div></el-tooltip><p class="field-hint">已报告 {{ result.paired_requests }} / {{ result.requests }} 次<span v-if="result.latest_at"> · {{ formatTime(result.latest_at, timezone) }}</span><span v-if="result.unreported_attempts || result.untracked_requests"> · 部分尝试缺少用量</span></p><p v-if="!result.requests" class="field-hint">接入后按真实用量显示，不影响策略配置。</p></template>
  </section>
</template>
<style scoped>
.recent-cache { border-top: 1px solid var(--rp-line); padding-top: 20px; margin-top: 20px; display: grid; gap: 10px; }
.cache-metric-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 16px; }
.cache-metric-row strong { font-size: 24px; font-weight: 600; }
.cache-ratio-track { height: 10px; border-radius: 999px; background: var(--rp-muted-surface); overflow: hidden; }
.cache-ratio-track > span { display: block; height: 100%; background: #709a84; }
</style>
