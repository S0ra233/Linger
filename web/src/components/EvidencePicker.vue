<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { EvidencePage, EvidenceRecord, EvidenceRef } from '../information-types'
import EditDialog from './EditDialog.vue'

const props = withDefaults(defineProps<{ instanceId: string; timezone: string; maxSelection?: number }>(), { maxSelection: 20 })
const open = defineModel<boolean>({ required: true })
const selection = defineModel<EvidenceRef[]>('selection', { required: true })
const editor = ref<InstanceType<typeof EditDialog> | null>(null)
const kind = ref<EvidenceRef['kind']>('input'), picked = ref<EvidenceRef[]>([])
const rows = ref<EvidenceRecord[]>([]), next = ref<number | null>(null), loading = ref(false), error = ref('')
const names = { input: '用户输入', sent: '确认送达', life: '生活事件', world_event: '已采纳世界事件', observation: '公开观察' }
let version = 0
const has = (row: EvidenceRef) => picked.value.some(item => item.kind === row.kind && item.id === row.id)
function toggle(row: EvidenceRef) {
  const index = picked.value.findIndex(item => item.kind === row.kind && item.id === row.id)
  if (index >= 0) picked.value.splice(index, 1)
  else if (picked.value.length < props.maxSelection) picked.value.push({ kind: row.kind, id: row.id, revision: row.revision })
}
async function load(more = false) {
  if (!open.value || !props.instanceId) return
  const mine = ++version; loading.value = true; error.value = ''
  if (!more) { rows.value = []; next.value = null }
  try {
    const page = await api<EvidencePage>(`/information/instances/${props.instanceId}/evidence?kind=${kind.value}${more && next.value ? '&before=' + next.value : ''}`)
    if (mine === version) { rows.value = more ? [...rows.value, ...page.items] : page.items; next.value = page.next_before }
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
watch(open, value => {
  if (value) { picked.value = selection.value.map(item => ({ ...item })); void load() }
  else { version++; loading.value = false }
}, { flush: 'sync' })
watch(kind, () => { void load() })
watch(() => props.instanceId, () => { version++; rows.value = []; next.value = null; picked.value = []; if (open.value) void load() })
onScopeDispose(() => { version++ })
function apply() { selection.value = picked.value.map(item => ({ ...item })); open.value = false }
</script>

<template>
  <EditDialog ref="editor" v-model="open" :draft="picked" title="选择补充来源" width="760px">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <div class="section-toolbar"><el-radio-group v-model="kind"><el-radio-button v-for="(name, value) in names" :key="value" :value="value">{{ name }}</el-radio-button></el-radio-group><span>已选 {{ picked.length }} / {{ maxSelection }}</span></div>
    <div v-loading="loading"><el-empty v-if="!rows.length && !loading" description="暂无来源" :image-size="60" />
      <article v-for="row in rows" :key="`${row.kind}-${row.id}`" class="check-record">
        <el-checkbox :model-value="has(row)" :disabled="!!row.validity?.invalid || (!has(row) && picked.length >= maxSelection)" @change="toggle(row)">{{ names[row.kind] }} #{{ row.id }} · {{ formatTime(row.at, timezone) }}</el-checkbox>
        <el-tag v-if="row.validity?.invalid" type="warning">已失效</el-tag><p class="message-text">{{ row.text || JSON.stringify(row.state_changes || {}) }}{{ row.truncated ? '…（预览截取）' : '' }}</p>
      </article>
    </div>
    <el-button v-if="next" :loading="loading" @click="load(true)">载入更多</el-button>
    <div class="form-actions"><el-button @click="editor?.close()">取消</el-button><el-button type="primary" @click="apply">采用选择</el-button></div>
  </EditDialog>
</template>
