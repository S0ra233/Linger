<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { ConversationWindow, EvidenceRef } from '../information-types'
const props = defineProps<{ instanceId: string; source: EvidenceRef; timezone: string; disabled?: boolean; label?: string }>()
const visible = ref(false), loading = ref(false), error = ref(''), result = ref<ConversationWindow | null>(null)
const reasons = { source_invalid: '包含失效来源', message_limit: '单轮消息过多', token_budget: '超出本次原文预算' }
let version = 0
async function load(page?: number, fresh = false) {
  const mine = ++version, target = props.instanceId, source = props.source
  loading.value = true; error.value = ''
  const query = new URLSearchParams({ kind: source.kind, id: String(source.id) })
  if (page != null) query.set('page', String(page))
  if (!fresh && result.value) {
    query.set('anchor_input', String(result.value.anchor_input)); query.set('anchor_sent', String(result.value.anchor_sent))
  }
  try {
    const data = await api<ConversationWindow>(`/information/instances/${target}/conversation-window?${query}`)
    if (mine === version) result.value = data
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function open() { result.value = null; visible.value = true; void load(undefined, true) }
function close() { version++; loading.value = false }
watch([() => props.instanceId, () => props.source.kind, () => props.source.id], () => { close(); visible.value = false; result.value = null })
onScopeDispose(close)
</script>
<template>
  <el-button v-if="source.kind === 'input' || source.kind === 'sent'" link :disabled="disabled" @click="open">{{ label ?? '查看原对话附近' }}</el-button>
  <el-drawer v-model="visible" title="原对话附近" size="min(820px, 94vw)" class="detail-drawer" append-to-body destroy-on-close @close="close">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <el-skeleton v-if="loading && !result" :rows="5" animated />
    <template v-if="result">
      <div class="conversation-pager"><el-pagination :current-page="result.page" :page-size="result.page_size" :total="result.total" :pager-count="5" :disabled="loading" layout="prev, pager, next" @current-change="load" /><el-button :loading="loading" @click="load(undefined, true)">返回来源</el-button></div>
      <p class="muted">来源位于第 {{ result.anchor_turn }} 轮 · 本页 {{ result.from_turn }}—{{ result.to_turn }} 轮</p>
      <el-alert v-if="result.omitted.length" :title="result.omitted.map(item => `第 ${item.number} 轮：${reasons[item.reason]}`).join('；')" type="info" :closable="false" class="form-alert" />
      <el-empty v-if="!result.items.length" description="本页没有可完整展示的轮次" />
      <section v-for="turn in result.items" :key="turn.number" class="conversation-turn" :aria-busy="loading">
        <div class="conversation-turn-label">第 {{ turn.number }} 轮</div>
        <article v-for="message in turn.messages" :key="`${message.kind}:${message.id}`" :class="['conversation-message', message.kind === 'sent' ? 'assistant' : 'user', { 'source-anchor': message.is_anchor }]">
          <div class="input-record-heading"><span><el-tag size="small" :type="message.kind === 'input' ? 'info' : 'success'" effect="plain">{{ message.kind === 'input' ? '用户' : '角色 · 已送达' }}</el-tag><el-tag v-if="message.is_anchor" size="small" effect="plain">来源</el-tag></span><time>{{ formatTime(message.at, timezone) }}</time></div>
          <p class="message-text">{{ message.text }}</p>
        </article>
      </section>
      <details class="raw-details"><summary>读取范围</summary><p class="muted">{{ result.meaning }}</p><p>返回内容粗估 {{ result.estimated_tokens.toLocaleString() }} / {{ result.token_budget.toLocaleString() }} tokens；每页最多 {{ result.page_size }} 个完整轮次。</p></details>
    </template>
  </el-drawer>
</template>
<style scoped>
.source-anchor { outline: 1px solid var(--el-color-primary); outline-offset: -1px; }
.input-record-heading > span { display: inline-flex; flex-wrap: wrap; gap: 8px; }
</style>
