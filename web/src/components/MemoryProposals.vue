<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { MemoryProposalPage, MemoryProposal } from '../information-types'
import EvidenceSources from './EvidenceSources.vue'
import DataDetails from './DataDetails.vue'
const props = defineProps<{ instanceId: string; timezone: string; refreshKey: number }>()
const emit = defineEmits<{ changed: [] }>()
const opened = ref(false), loaded = ref(false), loading = ref(false), error = ref(''), busy = ref(''), updated = ref(false)
const items = ref<MemoryProposal[]>([]), next = ref<string | null>(null)
const pendingCount = ref(0), pendingLimit = ref(200)
const displayed = computed(() => items.value.map(item => ({ ...item, proposal: data(item) })))
const labels = { pending: '待审核', applied: '已采用', rejected: '已拒绝', stale: '依据已变化' }
let version = 0, actionVersion = 0
function data(item: MemoryProposal) { try { return JSON.parse(item.proposal_json) } catch { return null } }
async function load(more = false) {
  if (!props.instanceId) return
  const mine = ++version, target = props.instanceId
  loading.value = true; error.value = ''
  try {
    const value = await api<MemoryProposalPage>(`/information/instances/${target}/memory-proposals${more && next.value ? '?before=' + next.value : ''}`)
    if (mine === version) { items.value = more ? [...items.value, ...value.items] : value.items; next.value = value.next_before; pendingCount.value = value.pending_count ?? 0; pendingLimit.value = value.pending_limit ?? 200; loaded.value = true; updated.value = false }
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function review(item: MemoryProposal, action: 'approve' | 'reject') {
  if (busy.value) return
  const mine = ++actionVersion, target = props.instanceId
  busy.value = item.id; error.value = ''
  try {
    await api(`/information/instances/${target}/memory-proposals/${item.id}/review`, { method: 'POST', body: JSON.stringify({ revision: item.revision, action }) })
    if (mine === actionVersion) { emit('changed'); await load() }
  } catch (cause) { if (mine === actionVersion) error.value = errorText(cause) }
  finally { if (mine === actionVersion) busy.value = '' }
}
function toggle(event: Event) { opened.value = (event.target as HTMLDetailsElement).open; if (opened.value && !loaded.value) void load() }
watch(() => props.instanceId, () => { version++; actionVersion++; items.value = []; next.value = null; pendingCount.value = 0; busy.value = ''; loaded.value = false; loading.value = false; error.value = ''; if (opened.value) void load() })
watch(() => props.refreshKey, () => { updated.value = true })
onScopeDispose(() => { version++; actionVersion++ })
</script>
<template>
  <details class="raw-details section-gap" @toggle="toggle"><summary>受保护记忆的修改提案 <el-tag v-if="updated" size="small" effect="plain">有变化</el-tag></summary>
    <template v-if="opened">
      <div class="section-toolbar"><span>待审 {{ pendingCount }} / {{ pendingLimit }} · 采用不会自动解锁</span><el-button :loading="loading" :disabled="!!busy" @click="load()">刷新</el-button></div>
      <el-alert v-if="pendingCount >= pendingLimit" title="待审队列已满；新候选保留在对应运行的已提交计划中，不会自动采用或补入队列。请先审核或拒绝待审项。" type="warning" :closable="false" class="form-alert" />
      <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
      <el-skeleton v-if="loading && !loaded" :rows="4" animated />
      <el-empty v-else-if="loaded && !items.length" description="暂无提案" />
      <article v-for="item in displayed" :key="item.id" class="check-record">
        <div class="section-toolbar"><span><el-tag :type="item.effective_status === 'pending' ? 'warning' : 'info'" effect="plain">{{ labels[item.effective_status] }}</el-tag> · 运行 #{{ item.run_id }} · {{ formatTime(item.created_at, timezone) }}</span><div v-if="item.status === 'pending'"><el-button type="primary" :disabled="!!busy || item.effective_status !== 'pending'" :loading="busy === item.id" @click="review(item, 'approve')">采用</el-button><el-button :disabled="!!busy" @click="review(item, 'reject')">拒绝</el-button></div></div>
        <p v-if="item.stale_reason" class="muted">{{ item.stale_reason }}</p>
        <template v-if="item.proposal"><p><strong>{{ item.proposal.operation === 'forget' ? '建议遗忘' : item.proposal.supersedes?.length ? '建议替代旧事实' : '建议修改' }}</strong></p><p class="message-text">{{ item.proposal.text }}</p><p v-if="item.proposal.valid_from != null">生效时间：{{ formatTime(item.proposal.valid_from, timezone) }}</p><EvidenceSources :instance-id="instanceId" :sources="item.proposal.sources ?? []" :timezone="timezone" /></template>
        <details class="raw-details"><summary>当前记忆与提案参数</summary><article v-for="target in item.targets" :key="target.id"><p>{{ target.locked ? '人工锁定 · ' : '' }}v{{ target.revision }} · {{ target.id }}</p><p class="message-text">{{ target.text }}</p></article><DataDetails :value="item.proposal_json" decode /></details>
      </article>
      <el-button v-if="next" :loading="loading" :disabled="!!busy" @click="load(true)">更早的提案</el-button>
    </template>
  </details>
</template>
