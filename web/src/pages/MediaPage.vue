<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import type { InputMessagesPage, RoleInstance } from '../roleplay-types'
import { selectedLifeLine, useEditorClose } from '../ui-state'
interface Asset { id: string; kind: string; name: string; description: string; revision: number; archived: number; mime: string | null }
defineProps<{ refreshKey: number; timezone: string }>()
const instances = ref<RoleInstance[]>([]), instanceId = ref(selectedLifeLine.value), items = ref<Asset[]>([]), error = ref(''), busy = ref(false), loading = ref(false)
const query = ref(''), archived = ref(false), file = ref<File | null>(null), name = ref(''), description = ref(''), kind = ref('sticker')
const importOpen = ref(false), received = ref<InputMessagesPage['items']>([]), source = ref(''), faceId = ref(''), editing = ref<Asset | null>(null), editOpen = ref(false)
const { close: closeImport } = useEditorClose(importOpen, () => busy.value)
const { close: closeEdit } = useEditorClose(editOpen, () => busy.value)
const labels: Record<string, string> = { sticker: '自定义表情包', market: 'QQ 原生表情包', image: '普通图片', face: 'QQ 内置表情' }
const filtered = computed(() => items.value.filter(item => !query.value || `${item.name} ${item.description}`.includes(query.value)))
let version = 0, importVersion = 0
async function load() {
  if (!instanceId.value) { items.value = []; return }
  const request = ++version; loading.value = true
  try { const data = await api<{ items: Asset[] }>(`/media?instance_id=${instanceId.value}&archived=${archived.value}`); if (request === version) items.value = data.items }
  catch (cause) { if (request === version) error.value = errorText(cause) } finally { if (request === version) loading.value = false }
}
function chooseFile(event: Event) { const chosen = (event.target as HTMLInputElement).files?.[0]; file.value = chosen ?? null; if (chosen && !name.value) name.value = chosen.name.slice(0, 80) }
async function openImport() {
  const request = ++importVersion, target = instanceId.value
  source.value = ''; file.value = null; name.value = ''; description.value = ''; faceId.value = ''; kind.value = 'sticker'; received.value = []; importOpen.value = true
  try { const data = await api<InputMessagesPage>(`/roleplay/instances/${target}/messages`); if (request === importVersion && importOpen.value && instanceId.value === target) received.value = data.items } catch (cause) { if (request === importVersion && importOpen.value) error.value = errorText(cause) }
}
async function create() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    if (file.value && ['sticker', 'image'].includes(kind.value)) {
      const parameters = new URLSearchParams({ instance_id: instanceId.value, name: name.value, description: description.value, kind: kind.value })
      await api(`/media/upload?${parameters}`, { method: 'POST', body: file.value }, 60000)
    } else {
      const [message, part] = source.value.split(':')
      await api('/media/import', { method: 'POST', body: JSON.stringify({ instance_id: instanceId.value, name: name.value, description: description.value, kind: kind.value,
        source_message_id: kind.value !== 'face' && message ? Number(message) : null, source_part: Number(part ?? 0), face_id: kind.value === 'face' ? faceId.value : null }) })
    }
    importOpen.value = false; file.value = null; await load()
  } catch (cause) { error.value = errorText(cause) } finally { busy.value = false }
}
async function save() {
  if (!editing.value || busy.value) return
  busy.value = true
  try { const item = editing.value; await api(`/media/${item.id}`, { method: 'PUT', body: JSON.stringify({ revision: item.revision, name: item.name, description: item.description, archived: !!item.archived }) }); editOpen.value = false; await load() }
  catch (cause) { error.value = errorText(cause) } finally { busy.value = false }
}
watch([instanceId, archived], load)
watch(kind, () => { file.value = null; source.value = '' })
watch(importOpen, value => { if (!value) importVersion++ })
onMounted(async () => { const request = ++version; try { const rows = await api<RoleInstance[]>('/roleplay/instances'); if (request !== version) return; instances.value = rows; const target = rows.some(item => item.id === instanceId.value) ? instanceId.value : rows[0]?.id ?? ''; if (target !== instanceId.value) instanceId.value = target; else await load() } catch (cause) { if (request === version) error.value = errorText(cause) } })
onUnmounted(() => { version++; importVersion++ })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <div class="section-toolbar"><el-select v-model="instanceId" :disabled="busy || importOpen || editOpen" filterable placeholder="选择生活线"><el-option v-for="item in instances" :key="item.id" :value="item.id" :label="`${item.display_name || item.nickname || item.peer_id} · ${item.character_name}`" /></el-select><div><el-button :disabled="!instanceId || busy" @click="load">刷新</el-button><el-button type="primary" :disabled="!instanceId || busy" @click="openImport">添加表情包／图片</el-button></div></div>
  <div class="section-toolbar"><el-input v-model="query" placeholder="搜索名称与说明" clearable /><el-checkbox v-model="archived">包含归档</el-checkbox></div>
  <el-skeleton v-if="loading" :rows="5" animated /><el-empty v-else-if="!filtered.length" description="暂无可用素材" />
  <div v-else class="media-grid"><button v-for="item in filtered" :key="item.id" class="media-tile panel" @click="editing = { ...item }; editOpen = true"><img v-if="item.mime" :src="`/api/v1/media/${item.id}/preview`" :alt="item.name" loading="lazy" /><div v-else class="media-native">{{ item.kind === 'market' ? 'QQ 贴纸' : 'QQ 表情' }}</div><strong>{{ item.name }}</strong><span class="muted">{{ labels[item.kind] }}{{ item.archived ? ' · 已归档' : '' }}</span></button></div>
  <el-dialog v-model="importOpen" title="添加素材" width="min(560px, 94vw)" :before-close="() => closeImport()" :close-on-click-modal="false" :close-on-press-escape="false" :show-close="!busy">
    <el-form label-position="top" :disabled="busy"><el-form-item label="类型"><el-select v-model="kind"><el-option v-for="(label, value) in labels" :key="value" :value="value" :label="label" /></el-select></el-form-item><el-form-item label="名称"><el-input v-model="name" maxlength="80" /></el-form-item><el-form-item label="情境与说明"><el-input v-model="description" type="textarea" maxlength="500" /></el-form-item>
    <el-form-item v-if="['sticker', 'image'].includes(kind)" label="上传原件"><input :key="kind" type="file" accept="image/png,image/jpeg,image/gif,image/webp" :disabled="busy" @change="chooseFile" /></el-form-item>
    <el-form-item v-if="kind !== 'face' && !file" label="或从本线近期收到的图片选入"><el-select v-model="source" filterable><template v-for="message in received" :key="message.id"><template v-for="(part, index) in message.parts" :key="index"><el-option v-if="part.type === 'image'" :value="`${message.id}:${index}`" :label="`消息 ${message.id} · 图片 ${index + 1}`" /></template></template></el-select></el-form-item>
    <el-form-item v-if="kind === 'face'" label="已确认的 QQ 表情编号"><el-input v-model="faceId" maxlength="6" /></el-form-item></el-form>
    <template #footer><el-button :disabled="busy" @click="closeImport">取消</el-button><el-button type="primary" :loading="busy" @click="create">保存素材</el-button></template>
  </el-dialog>
  <el-dialog v-model="editOpen" :before-close="() => closeEdit()" title="素材详情" width="min(560px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="false" :show-close="!busy"><el-form v-if="editing" label-position="top" :disabled="busy"><img v-if="editing.mime" class="media-preview" :src="`/api/v1/media/${editing.id}/preview`" :alt="editing.name" /><el-form-item label="名称"><el-input v-model="editing.name" maxlength="80" /></el-form-item><el-form-item label="情境与说明"><el-input v-model="editing.description" type="textarea" maxlength="500" /></el-form-item><el-checkbox :model-value="!!editing.archived" @update:model-value="(value: boolean | string | number) => { if (editing) editing.archived = value ? 1 : 0 }">归档</el-checkbox></el-form><template #footer><el-button :disabled="busy" @click="closeEdit">取消</el-button><el-button type="primary" :loading="busy" @click="save">保存</el-button></template></el-dialog>
</template>
