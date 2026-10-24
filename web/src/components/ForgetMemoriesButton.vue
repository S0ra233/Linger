<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText } from '../api'
import EditDialog from './EditDialog.vue'
const props = defineProps<{ instanceId: string; disabled?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
type Kind = 'profile' | 'temporary' | 'episode'
interface Preview { counts: Record<Kind, number>; total: number; locked: number; token: string }
const kinds = ref<Kind[]>([]), visible = ref(false), loading = ref(false), busy = ref(false), error = ref(''), preview = ref<Preview | null>(null)
const choices: { value: Kind; label: string }[] = [{ value: 'profile', label: '长期画像' }, { value: 'temporary', label: '旧临时记忆' }, { value: 'episode', label: '情景记忆' }]
let version = 0
function open() { version++; kinds.value = []; preview.value = null; loading.value = false; error.value = ''; visible.value = true }
async function load() {
  const mine = ++version, id = props.instanceId, selected = [...kinds.value]
  preview.value = null; error.value = ''; loading.value = !!selected.length
  if (!selected.length) return
  try { const value = await api<Preview>(`/information/instances/${id}/memory-forget-preview`, { method: 'POST', body: JSON.stringify({ kinds: selected }) }); if (mine === version && id === props.instanceId) preview.value = value }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function forget() {
  const chosen = preview.value, id = props.instanceId
  if (!chosen?.total || busy.value || loading.value) return
  const mine = ++version; busy.value = true; error.value = ''
  try {
    const result = await api<{ count: number }>(`/information/instances/${id}/memory-forget-batch`, { method: 'POST', body: JSON.stringify({ kinds: kinds.value, token: chosen.token }) })
    if (mine !== version || id !== props.instanceId) return
    visible.value = false; emit('changed'); ElMessage.success(`已遗忘 ${result.count} 条记忆`)
  } catch (cause) { if (mine === version) { error.value = errorText(cause); preview.value = null } }
  finally { if (mine === version) busy.value = false }
}
watch(kinds, () => { if (visible.value && !busy.value) void load() }, { deep: true })
watch(() => props.instanceId, () => { version++; visible.value = false; busy.value = false; loading.value = false })
onScopeDispose(() => { version++ })
</script>
<template>
  <el-button :disabled="disabled || !instanceId" @click="open">遗忘记忆</el-button>
  <EditDialog v-model="visible" title="批量遗忘记忆" width="540px" :draft="null" :busy="busy">
    <p class="field-hint">选择需要遗忘的类型。历史版本与原始对话保留，所选记忆停止召回。</p>
    <el-checkbox-group v-model="kinds" class="memory-kind-options" :disabled="busy"><el-checkbox v-for="choice in choices" :key="choice.value" :value="choice.value" border>{{ choice.label }}</el-checkbox></el-checkbox-group>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <p class="batch-memory-count" aria-live="polite">{{ loading ? '正在统计…' : preview ? `选中 ${preview.total} 条记忆` : '请选择类型' }}<span v-if="preview?.locked" class="muted"> · 包含 {{ preview.locked }} 条人工锁定记忆</span></p>
    <div class="form-actions"><el-button :disabled="busy" @click="visible = false">取消</el-button><el-button v-if="error && kinds.length" :loading="loading" @click="load">重新读取</el-button><el-button type="danger" :loading="busy" :disabled="loading || !preview?.total" @click="forget">删除选中的记忆</el-button></div>
  </EditDialog>
</template>
