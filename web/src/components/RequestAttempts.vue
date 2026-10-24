<script setup lang="ts">
import { computed } from 'vue'
import { formatTime } from '../api'
interface AttemptEvent {
  request_id: string; attempt: number; phase: 'started' | 'received' | 'failed' | 'cancelled'
  at: number; http_status: number | null; retrying: boolean; retry_delay_seconds: number | null
  token_usage?: { input_tokens?: number | null; output_tokens?: number | null; reasoning_tokens?: number | null }
  response_summary?: { kind?: string; status?: string; output_item_types?: string[] }
}
const props = defineProps<{ value: string | null; timezone: string }>()
const attempts = computed(() => {
  try {
    const value = props.value ? JSON.parse(props.value)?.attempts : null
    return value?.tracked === true && Number.isInteger(value.started) ? value as { started: number; events?: AttemptEvent[] } : null
  } catch { return null }
})
const events = computed(() => Array.isArray(attempts.value?.events) ? attempts.value.events.filter(event =>
  event && Number.isInteger(event.attempt) && event.attempt > 0 && Number.isFinite(event.at)
  && ['started', 'received', 'failed', 'cancelled'].includes(event.phase)) : [])
const labels = { started: '开始请求', received: '收到响应', failed: '请求失败', cancelled: '已取消' }
</script>
<template>
  <section v-if="attempts" class="request-attempts">
    <div class="section-toolbar"><h3>请求尝试</h3><el-tag effect="plain" type="info">已请求 {{ attempts.started }} 次</el-tag></div>
    <ol v-if="events.length" class="attempt-events">
      <li v-for="(event, index) in events" :key="index">
        <time>{{ formatTime(event.at, timezone) }}</time>
        <span>第 {{ event.attempt }} 次 · {{ labels[event.phase] }}<template v-if="event.http_status"> · HTTP {{ event.http_status }}</template></span>
        <span v-if="event.retrying" class="muted">{{ event.retry_delay_seconds == null ? '计划重试' : `计划等待 ${event.retry_delay_seconds} 秒后重试` }}</span>
        <span v-if="event.response_summary?.kind === 'empty_response'" class="muted" :title="event.response_summary.output_item_types?.join(' / ')">空响应 · 未获得最终内容</span>
        <span v-if="event.token_usage" class="muted">输入 {{ event.token_usage.input_tokens ?? '未知' }} / 输出 {{ event.token_usage.output_tokens ?? '未知' }}<template v-if="event.token_usage.reasoning_tokens != null">（含思考 {{ event.token_usage.reasoning_tokens }}）</template></span>
      </li>
    </ol>
    <p v-if="events.some(event => event.retrying)" class="field-hint">等待可能被新消息或总时限中断；“开始请求”才计入实际次数。</p>
  </section>
</template>
<style scoped>
.request-attempts { margin: 18px 0 24px; }
.attempt-events { list-style: none; margin: 0; padding: 0; }
.attempt-events li { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.attempt-events time { color: var(--el-text-color-secondary); font-variant-numeric: tabular-nums; }
</style>
