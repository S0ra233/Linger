<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import EditDialog from './EditDialog.vue'
import { api, errorText, formatTime } from '../api'
const props = defineProps<{ instanceId: string; timezone: string }>()
const emit = defineEmits<{ cleared: [] }>()
interface Preview { token: string; rounds: number; inputs: number; sent: number; from_at: number | null; to_at: number | null; sending: boolean; pending_messages: number; pending_deliveries: number }
const visible = ref(false), loading = ref(false), busy = ref(false), error = ref(''), preview = ref<Preview | null>(null)
let version = 0
async function open() {
  visible.value = true; error.value = ''; preview.value = null
  const mine = ++version, id = props.instanceId
  loading.value = true
  try { const value = await api<Preview>(`/roleplay/instances/${id}/conversation/clear-preview`); if (mine === version && id === props.instanceId) preview.value = value }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function clear() {
  const chosen = preview.value, id = props.instanceId
  if (!chosen?.rounds || chosen.sending || busy.value) return
  const mine = ++version
  busy.value = true; error.value = ''
  try {
    await api(`/roleplay/instances/${id}/conversation/clear`, { method: 'POST', body: JSON.stringify({ token: chosen.token }) })
    if (mine !== version || id !== props.instanceId) return
    visible.value = false; emit('cleared'); ElMessage.success('上下文已清空，对话已归档')
  } catch (cause) { if (mine === version) { error.value = errorText(cause); preview.value = null } }
  finally { if (mine === version) busy.value = false }
}
watch(() => props.instanceId, () => { version++; visible.value = false; loading.value = false; busy.value = false })
onScopeDispose(() => { version++ })
</script>
<template>
  <el-button @click="open">清空上下文</el-button>
  <EditDialog v-model="visible" title="清空对话上下文" width="560px" :draft="null" :busy="busy">
    <p class="message-text">移除旧对话在模型上下文中的占用。归档后可在「设置 → 已归档对话」查看，但暂不支持恢复到当前对话。</p>
    <p class="field-hint">角色卡、世界观、预设和私有记忆保留。短期分支与长期待发安排一并取消，QQ记录保持原样。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <el-skeleton v-if="loading" :rows="2" animated />
    <dl v-else-if="preview" class="detail-list"><div><dt>本次归档</dt><dd>{{ preview.rounds }} 轮 · {{ preview.inputs }} 条输入 · {{ preview.sent }} 条已送达</dd></div><div v-if="preview.from_at != null && preview.to_at != null"><dt>记录范围</dt><dd>{{ formatTime(preview.from_at, timezone) }} — {{ formatTime(preview.to_at, timezone) }}</dd></div></dl>
    <el-alert v-if="preview?.sending" title="消息正在投递，请等回执返回后再清空。" type="warning" :closable="false" />
    <div class="form-actions"><el-button :disabled="busy" @click="visible = false">取消</el-button><el-button v-if="error && !preview" :loading="loading" @click="open">重新读取</el-button><el-button type="danger" :loading="busy" :disabled="loading || !preview?.rounds || preview.sending" @click="clear">清空并归档</el-button></div>
  </EditDialog>
</template>
