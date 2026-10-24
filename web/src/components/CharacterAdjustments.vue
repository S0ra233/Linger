<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { EvidenceRef } from '../information-types'
import EvidenceSources from './EvidenceSources.vue'
const props = defineProps<{ instanceId: string; timezone: string; refreshKey: number }>()
interface Proposal { id: number; revision: number; reason: string; changes: Record<string, string>; before: Record<string, string>; sources: EvidenceRef[]; status: string; stale: boolean; created_at: number }
interface Details { revision: number; character_revision: number; overrides: Record<string, string>; sources_valid: boolean; items: Proposal[]; next_before: number | null; versions: { revision: number; reason: string; created_at: number }[] }
const data = ref<Details | null>(null), error = ref(''), loading = ref(false), busy = ref(false), changed = ref(false)
const names: Record<string, string> = { description: '描述', personality: '性格', scenario: '场景', style: '表达风格', dialogue_examples: '示例对话' }
const states: Record<string, string> = { pending: '待审核', adopted: '已采用', rejected: '已拒绝' }
let version = 0, disposed = false
async function load(more = false) {
  const mine = ++version, target = props.instanceId; loading.value = true
  try {
    const value = await api<Details>(`/information/instances/${target}/character-adjustments${more && data.value?.next_before ? '?before=' + data.value.next_before : ''}`)
    if (mine === version) { if (more && data.value) value.items = [...data.value.items, ...value.items]; data.value = value; error.value = ''; changed.value = false }
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function mutate(path: string, body: object, message: string) {
  if (busy.value || !data.value) return
  const target = props.instanceId, mine = version; busy.value = true
  try {
    try { await ElMessageBox.confirm(message, '调整当前生活线', { confirmButtonText: '确认', cancelButtonText: '取消' }) } catch { return }
    if (target !== props.instanceId || mine !== version || disposed) return
    await api(`/information/instances/${target}/character-adjustments/${path}`, { method: 'POST', body: JSON.stringify(body) })
    if (target === props.instanceId && !disposed) await load()
  } catch (cause) { if (target === props.instanceId && !disposed) error.value = errorText(cause) }
  finally { busy.value = false }
}
watch(() => props.instanceId, () => { data.value = null; void load() }, { immediate: true })
watch(() => props.refreshKey, () => { changed.value = true })
onScopeDispose(() => { version++; disposed = true })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span>联系人局部设定 <template v-if="data">v{{ data.revision }} · 模板 v{{ data.character_revision }}</template></span><el-button :disabled="busy" :loading="loading" @click="load()">{{ changed ? '有变化 · 刷新' : '刷新' }}</el-button></div>
  <div v-if="data" v-loading="loading">
    <el-alert v-if="!data.sources_valid" title="局部设定来源失效，当前采用原模板。请撤销到有效版本。" type="warning" :closable="false" class="form-alert" />
    <el-collapse class="quiet-collapse"><el-collapse-item title="当前修改与历史版本" name="versions"><article v-for="(value, key) in data.overrides" :key="key" class="check-record"><strong>{{ names[key] || key }}</strong><p class="message-text">{{ value }}</p></article><div class="section-toolbar"><span>原角色模板</span><el-button :disabled="busy || !data.revision" @click="mutate('rollback', { revision: data.revision, target_revision: 0 }, '撤销本线全部局部修改，保留历史版本。')">恢复模板</el-button></div><article v-for="item in data.versions" :key="item.revision" class="section-toolbar"><span>v{{ item.revision }} · {{ item.reason }} · {{ formatTime(item.created_at, timezone) }}</span><el-button :disabled="busy || data.revision === item.revision" @click="mutate('rollback', { revision: data.revision, target_revision: item.revision }, '将本线局部修改恢复到所选版本，并保存新的撤销记录。')">恢复此版本</el-button></article></el-collapse-item></el-collapse>
    <el-empty v-if="!data.items.length" description="暂无角色调整提案" />
    <article v-for="row in data.items" :key="row.id" class="check-record"><div class="section-toolbar"><strong>提案 #{{ row.id }}</strong><el-tag effect="plain">{{ states[row.status] || row.status }}{{ row.status === 'pending' && row.stale ? ' · 已过期' : '' }}</el-tag></div><p>{{ row.reason }}</p><EvidenceSources :instance-id="instanceId" :sources="row.sources" :timezone="timezone" /><el-collapse><el-collapse-item v-for="(value, key) in row.changes" :key="key" :title="names[key] || key" :name="key"><div class="form-two-columns"><div><strong>修改前</strong><p class="message-text">{{ row.before[key] || '空' }}</p></div><div><strong>修改后</strong><p class="message-text">{{ value || '空' }}</p></div></div></el-collapse-item></el-collapse><div v-if="row.status === 'pending'" class="form-actions"><el-button :disabled="busy" @click="mutate(`${row.id}/review`, { revision: row.revision, action: 'reject' }, '拒绝此提案。')">拒绝</el-button><el-button type="primary" :disabled="busy || row.stale || !data.sources_valid" @click="mutate(`${row.id}/review`, { revision: row.revision, action: 'adopt' }, '只更新当前联系人局部设定，取消旧运行和未发气泡。')">采用</el-button></div></article>
    <el-button v-if="data.next_before" :loading="loading" :disabled="busy" @click="load(true)">更早的提案</el-button>
  </div>
</template>
