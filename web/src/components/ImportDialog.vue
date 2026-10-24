<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import DataDetails from './DataDetails.vue'
import { api, errorText } from '../api'
import type { MaterialImport } from '../material-types'

const props = defineProps<{ kind: 'character' | 'worldbook'; source?: MaterialImport | null }>()
const visible = defineModel<boolean>({ default: false })
const emit = defineEmits<{ committed: [] }>()
const record = ref<MaterialImport | null>(null), busy = ref(false), error = ref(''), picker = ref<HTMLInputElement | null>(null)
const title = computed(() => props.source ? '原始导入资料' : props.kind === 'character' ? '导入角色卡' : '导入世界书')
const metadata = computed(() => record.value?.kind === 'character' ? record.value.normalized.metadata : null)
const mapped = computed(() => record.value?.kind === 'character' ? record.value.normalized.character : record.value?.normalized)
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
watch(visible, value => { if (value) { record.value = props.source ?? null; error.value = ''; if (picker.value) picker.value.value = '' } })
async function choose(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  record.value = null; error.value = ''
  if (file.size > 12 * 1024 * 1024) { error.value = '导入文件不能超过 12 MiB。'; return }
  busy.value = true
  try { record.value = await api<MaterialImport>(`/roleplay/imports/preview?kind=${props.kind}&filename=${encodeURIComponent(file.name.slice(0, 200))}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } }, 60000) }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
async function commit() {
  if (!record.value || busy.value) return
  busy.value = true; error.value = ''
  try { await api(`/roleplay/imports/${record.value.id}/commit`, { method: 'POST' }); emit('committed'); visible.value = false; ElMessage.success('已导入') }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="800px" class="model-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <template v-if="!source"><input ref="picker" type="file" :accept="kind === 'character' ? '.json,.png' : '.json'" hidden @change="choose" /><el-button :loading="busy" @click="picker?.click()">选择{{ kind === 'character' ? ' JSON／PNG' : ' JSON' }} 文件</el-button></template>
    <template v-if="record">
      <div class="section-toolbar"><strong>{{ record.filename }}</strong><a :href="`/api/v1/roleplay/imports/${record.id}/original`" download>下载原文件</a></div>
      <el-alert v-if="record.warnings.length" type="warning" :closable="false" class="form-alert"><ul class="import-warnings"><li v-for="warning in record.warnings" :key="warning">{{ warning }}</li></ul></el-alert>
      <el-collapse :model-value="['mapped']">
        <el-collapse-item title="将采用的资料" name="mapped"><DataDetails :value="mapped" /><p v-if="record.kind === 'character' && record.normalized.worldbook">包含关联世界书，将一并创建并绑定。</p></el-collapse-item>
        <el-collapse-item v-if="record.kind === 'character' && record.normalized.worldbook" title="关联世界书预览" name="book"><DataDetails :value="record.normalized.worldbook" /></el-collapse-item>
        <el-collapse-item v-if="metadata && Object.keys(metadata).length" title="作者说明与元数据（不进入提示词）" name="metadata"><pre class="diagnostic-json">{{ pretty(metadata) }}</pre></el-collapse-item>
        <el-collapse-item title="完整原始 JSON" name="raw"><pre class="diagnostic-json">{{ pretty(record.raw) }}</pre><p class="source-fingerprint">SHA-256 {{ record.source_hash }}</p></el-collapse-item>
      </el-collapse>
    </template>
    <div class="form-actions editor-actions"><el-button :disabled="busy" @click="visible = false">关闭</el-button><el-button v-if="!source" type="primary" :loading="busy" :disabled="!record" @click="commit">确认导入</el-button></div>
  </el-dialog>
</template>
