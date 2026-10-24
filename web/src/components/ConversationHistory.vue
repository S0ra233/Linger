<script setup lang="ts">
import { onMounted, onScopeDispose, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import MediaMessage from './MediaMessage.vue'
import EditDialog from './EditDialog.vue'
import type { MessagePart } from '../roleplay-types'
const props = defineProps<{ instanceId: string; refreshKey: number; timezone: string; archiveId?: string; expanded?: boolean }>()
const emit = defineEmits<{ deleted: [] }>()
interface Message { id: number; role: 'user' | 'assistant'; message_id: string; at: number; text: string; parts: MessagePart[] }
interface History { items: { number: number; messages: Message[] }[]; total: number; page: number; page_size: number; anchor_input: number; anchor_sent: number }
const opened = ref(!!props.expanded), loading = ref(false), error = ref(''), updates = ref(false)
const result = ref<History | null>(null), page = ref(1), size = ref(30)
interface DeletePreview { token: string; rounds: number; total: number; remaining: number; inputs: number; sent: number; from_at: number; to_at: number; sending: boolean }
const deleteOpen = ref(false), deleteBusy = ref(false), deleteLoading = ref(false), deleteError = ref(''), deleteRounds = ref(1)
const deletion = ref<DeletePreview | null>(null), deleteDialog = ref<InstanceType<typeof EditDialog> | null>(null)
let version = 0, deleteVersion = 0
async function load(fresh = false, targetPage = page.value) {
  if (loading.value) return
  const id = props.instanceId, mine = ++version
  const query = new URLSearchParams({ page: String(fresh ? 1 : targetPage), page_size: String(size.value) })
  if (!fresh && result.value) { query.set('anchor_input', String(result.value.anchor_input)); query.set('anchor_sent', String(result.value.anchor_sent)) }
  loading.value = true; error.value = ''
  try {
    const path = props.archiveId ? `/roleplay/conversation-archives/${props.archiveId}` : `/roleplay/instances/${id}/conversation`
    const value = await api<History>(`${path}?${query}`)
    if (mine !== version || id !== props.instanceId) return
    result.value = value; page.value = value.page
    if (fresh) updates.value = false
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function changePage(value: number) { void load(false, value) }
function changeSize(value: number) { size.value = value; void load(false, 1) }
function openDelete() { deleteRounds.value = 1; deletion.value = null; deleteError.value = ''; deleteOpen.value = true }
async function previewDelete() {
  if (deleteLoading.value || deleteBusy.value) return
  const target = props.instanceId, mine = ++deleteVersion, rounds = deleteRounds.value
  deleteLoading.value = true; deleteError.value = ''; deletion.value = null
  try {
    const value = await api<DeletePreview>(`/roleplay/instances/${target}/conversation/delete-preview?rounds=${rounds}`)
    if (mine === deleteVersion && target === props.instanceId && rounds === deleteRounds.value) deletion.value = value
  } catch (cause) { if (mine === deleteVersion) deleteError.value = errorText(cause) }
  finally { if (mine === deleteVersion) deleteLoading.value = false }
}
async function removeRecent() {
  const chosen = deletion.value, target = props.instanceId
  if (!chosen || chosen.rounds !== deleteRounds.value || chosen.sending || deleteBusy.value || deleteLoading.value) return
  const mine = ++deleteVersion
  deleteBusy.value = true; deleteError.value = ''
  try {
    await api(`/roleplay/instances/${target}/conversation/delete-recent`, { method: 'POST', body: JSON.stringify({ rounds: chosen.rounds, token: chosen.token }) })
    if (mine !== deleteVersion || target !== props.instanceId) return
    deleteOpen.value = false; result.value = null; page.value = 1; emit('deleted')
    ElMessage.success(`已从 RP 对话中删除最近 ${chosen.rounds} 轮`); await load(true)
  } catch (cause) { if (mine === deleteVersion) { deleteError.value = errorText(cause); deletion.value = null } }
  finally { if (mine === deleteVersion) deleteBusy.value = false }
}
function toggle(event: Event) {
  opened.value = (event.target as HTMLDetailsElement).open
  if (opened.value && !result.value && !loading.value) void load(true)
}
watch(deleteRounds, () => { deleteVersion++; deletion.value = null; deleteLoading.value = false; deleteError.value = '' })
watch(() => props.instanceId, () => { version++; deleteVersion++; deleteOpen.value = false; deleteBusy.value = false; deleteLoading.value = false; deletion.value = null; result.value = null; page.value = 1; loading.value = false; updates.value = false; if (opened.value) void load(true) })
watch(() => props.refreshKey, () => { updates.value = true })
onScopeDispose(() => { version++; deleteVersion++ })
onMounted(() => { if (opened.value) void load(true) })
</script>
<template>
  <details class="conversation-fold" :open="opened" @toggle="toggle">
    <summary>对话记录 <el-tag v-if="result" size="small" effect="plain">{{ result.total }} 轮次</el-tag></summary>
    <template v-if="opened">
      <div class="conversation-pager"><el-pagination :current-page="page" :page-size="size" :page-sizes="[20, 30, 50]" :total="result?.total ?? 0" :pager-count="5" :disabled="loading" layout="sizes, prev, pager, next" @current-change="changePage" @size-change="changeSize" /><div class="inline-actions"><el-button :type="updates ? 'primary' : 'default'" :loading="loading" @click="load(true)">{{ updates ? '有变化 · 查看最新' : '查看最新' }}</el-button><el-button v-if="!archiveId" type="danger" plain :disabled="!result?.total || loading || deleteBusy" @click="openDelete">删除最近轮次</el-button></div></div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-skeleton v-if="loading && !result" :rows="4" animated />
      <el-empty v-else-if="result && !result.items.length" description="暂无对话记录" />
      <section v-for="turn in result?.items ?? []" :key="turn.number" class="conversation-turn" :aria-busy="loading">
        <div class="conversation-turn-label">第 {{ turn.number }} 轮</div>
        <article v-for="message in turn.messages" :key="`${message.role}:${message.id}`" :class="['conversation-message', message.role]">
          <div class="input-record-heading"><el-tag size="small" :type="message.role === 'user' ? 'info' : 'success'" effect="plain">{{ message.role === 'user' ? '用户' : '角色 · 已送达' }}</el-tag><time>{{ formatTime(message.at, timezone) }}</time></div>
          <template v-if="message.role === 'assistant'"><p v-if="message.text" class="message-text">{{ message.text }}</p><MediaMessage :parts="JSON.stringify(message.parts)" /></template>
          <template v-else><div v-for="(part, index) in message.parts" :key="index"><p v-if="part.type === 'text'" class="message-text">{{ part.text }}</p><el-image v-else-if="part.type === 'image' && part.status === 'ready' && part.url" :src="part.url" :preview-src-list="[part.url]" preview-teleported fit="contain" class="message-image" /><span v-else-if="part.type === 'image'" class="muted">{{ part.error || '图片接收中' }}</span><span v-else-if="part.type === 'reply'" class="muted">引用 {{ part.message_id }}</span><span v-else class="muted">暂不支持：{{ part.kind }}</span></div></template>
        </article>
      </section>
    </template>
  </details>
  <EditDialog ref="deleteDialog" v-model="deleteOpen" title="删除最近对话" width="560px" :draft="null" :busy="deleteBusy || deleteLoading">
    <el-alert v-if="deleteError" :title="deleteError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="deleteBusy || deleteLoading" @submit.prevent="previewDelete">
      <el-form-item label="最近多少完整轮次"><div class="inline-actions"><el-input-number v-model="deleteRounds" :min="1" :max="Math.min(result?.total || 1, 1000000)" :precision="0" :value-on-clear="1" /><el-button native-type="submit" :loading="deleteLoading">预览范围</el-button></div></el-form-item>
      <p class="field-hint">角色卡、世界观和预设保留；QQ记录及旧运行诊断保留。</p>
      <template v-if="deletion"><dl class="detail-list"><div><dt>删除范围</dt><dd>{{ formatTime(deletion.from_at, timezone) }} — {{ formatTime(deletion.to_at, timezone) }}</dd></div><div><dt>消息数量</dt><dd>{{ deletion.inputs }} 条输入、{{ deletion.sent }} 个已送达气泡</dd></div><div><dt>删除后剩余</dt><dd>{{ deletion.remaining }} 轮</dd></div></dl><el-alert :title="deletion.sending ? '有消息正在投递，请等待回执后重新预览。' : '所选内容不再供模型读取；受影响的记忆、状态与计划会停止采用。'" :type="deletion.sending ? 'warning' : 'info'" :closable="false" class="form-alert" /></template>
      <div class="form-actions"><el-button @click="deleteDialog?.close()">取消</el-button><el-button type="danger" :loading="deleteBusy" :disabled="!deletion || deletion.sending || deleteLoading || deletion.rounds !== deleteRounds" @click="removeRecent">确认删除 {{ deletion?.rounds ?? deleteRounds }} 轮</el-button></div>
    </el-form>
  </EditDialog>
</template>
