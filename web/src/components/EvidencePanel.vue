<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import ConversationWindowLink from './ConversationWindowLink.vue'
import type { EvidencePage, EvidenceRecord, EvidenceRef } from '../information-types'
const props = defineProps<{ instanceId: string; timezone: string; refreshKey: number }>()
const kind = ref<EvidenceRef['kind']>('input'), items = ref<EvidenceRecord[]>([]), next = ref<number | null>(null)
const error = ref(''), loading = ref(false), busy = ref(false), changed = ref(false)
const names = { input: '用户输入', sent: '确认送达', life: '生活事件', world_event: '已采纳世界事件', observation: '公开观察' }
let version = 0, disposed = false
async function load(more = false) {
  const target = props.instanceId, category = kind.value, mine = ++version
  loading.value = true; error.value = ''
  try {
    const data = await api<EvidencePage>(`/information/instances/${target}/evidence?kind=${category}${more && next.value ? '&before=' + next.value : ''}`)
    if (mine === version) { items.value = more ? [...items.value, ...data.items] : data.items; next.value = data.next_before; changed.value = false }
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function change(row: EvidenceRecord) {
  if (busy.value) return
  const target = props.instanceId, invalid = !row.validity?.invalid, mine = version
  busy.value = true
  try {
    let reason: string
    try {
      const result = await ElMessageBox.prompt(invalid ? '原文保留；停用相关记忆、旧摘要、状态及场景，取消引用它的待办和未发气泡。填写原因：' : '来源可重新被引用；旧摘要、状态、场景和已取消计划不会自动恢复。填写原因：', invalid ? '标记来源失效' : '恢复来源', { inputValidator: value => !!value?.trim() && value.length <= 1000 || '请填写 1—1000 字符的原因', confirmButtonText: '确认', cancelButtonText: '取消' })
      reason = result.value
    } catch { return }
    if (target !== props.instanceId || mine !== version || disposed) return
    await api(`/information/instances/${target}/evidence/${row.kind}/${row.id}/status`, { method: 'PUT', body: JSON.stringify({ invalid, reason, revision: row.validity?.revision ?? 0 }) })
    if (target === props.instanceId && !disposed) await load()
  } catch (cause) { if (target === props.instanceId && !disposed) error.value = errorText(cause) }
  finally { busy.value = false }
}
watch([() => props.instanceId, kind], () => { items.value = []; next.value = null; void load() }, { immediate: true })
watch(() => props.refreshKey, () => { changed.value = true })
onScopeDispose(() => { version++; disposed = true })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><el-radio-group v-model="kind" :disabled="busy"><el-radio-button v-for="(label, key) in names" :key="key" :value="key">{{ label }}</el-radio-button></el-radio-group><el-button :loading="loading" :disabled="busy" @click="load()">{{ changed ? '有变化 · 刷新' : '刷新' }}</el-button></div>
  <div v-loading="loading"><el-empty v-if="!items.length" description="暂无来源记录" />
    <article v-for="row in items" :key="`${row.kind}-${row.id}`" class="check-record"><div class="section-toolbar"><span>#{{ row.id }} · {{ formatTime(row.at, timezone) }}</span><div><el-tag v-if="row.validity?.invalid" type="warning">已失效</el-tag><el-button link :disabled="busy" @click="change(row)">{{ row.validity?.invalid ? '恢复来源' : '标记失效' }}</el-button></div></div><ConversationWindowLink :instance-id="instanceId" :source="row" :timezone="timezone" :disabled="!!row.validity?.invalid || row.deleted" /><p class="message-text">{{ row.text }}{{ row.truncated ? '…' : '' }}</p><p v-if="row.validity?.reason">{{ row.validity.reason }}</p></article>
  </div><el-button v-if="next" :loading="loading" :disabled="busy" @click="load(true)">更早的来源</el-button>
</template>
