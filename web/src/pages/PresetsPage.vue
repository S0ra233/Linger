<script setup lang="ts">
import EditorWorkspace from '../components/EditorWorkspace.vue'
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { PresetCatalog, PresetDraft, SavedPreset } from '../preset-types'
import PresetConfigEditor from '../components/PresetConfigEditor.vue'
const editor = ref<InstanceType<typeof EditorWorkspace> | null>(null)
const props = defineProps<{ refreshKey: number; timezone: string }>()
const catalog = ref<PresetCatalog | null>(null), error = ref(''), editorError = ref(''), loading = ref(false), busy = ref(false), visible = ref(false)
const draft = ref<PresetDraft | null>(null), editing = ref<string | null>(null), picker = ref<HTMLInputElement | null>(null)
const syncWarning = ref('')
let version = 0, editVersion = 0
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
async function load() {
  const mine = ++version; loading.value = true
  try { const value = await api<PresetCatalog>('/models/presets'); if (mine === version) { catalog.value = value; error.value = '' } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function edit(id?: string) {
  const mine = ++editVersion; editorError.value = ''
  if (!id) { if (!catalog.value) return; editing.value = null; draft.value = { name: '', config: clone(catalog.value.defaults), revision: 0, source: null }; visible.value = true; return }
  try { const row = await api<SavedPreset>(`/models/presets/${id}`); if (mine !== editVersion) return; editing.value = id; draft.value = { name: row.name, config: row.config, revision: row.revision, source: row.source }; visible.value = true }
  catch (cause) { error.value = errorText(cause) }
}
async function choose(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return
  if (file.size > 2 * 1024 * 1024) { error.value = '预设 JSON 不能超过 2 MiB。'; return }
  busy.value = true; const mine = ++editVersion
  try { const value = await api<PresetDraft>(`/models/presets/import-preview?filename=${encodeURIComponent(file.name.slice(0, 200))}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } }, 60000); if (mine === editVersion) { draft.value = value; editing.value = null; editorError.value = ''; visible.value = true } }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false; if (picker.value) picker.value.value = '' }
}
async function save(copy = false) {
  if (!draft.value || busy.value) return
  busy.value = true; editorError.value = ''
  try {
    const value = await api<SavedPreset & { sync?: { pending: { name: string }[] } }>(`/models/presets${editing.value && !copy ? '/' + editing.value : ''}`, { method: editing.value && !copy ? 'PUT' : 'POST', body: JSON.stringify({ ...draft.value, revision: copy ? 0 : draft.value.revision }) })
    syncWarning.value = value.sync?.pending.length ? `${value.sync.pending.map(item => item.name).join('、')} 仍保留原预设，请在模型设置中同步并调整参数。` : ''
    visible.value = false; ElMessage.success(editing.value && !copy ? '预设已保存，兼容的模型已同步' : '预设已保存'); await load()
  }
  catch (cause) { editorError.value = errorText(cause) }
  finally { busy.value = false }
}
watch(() => props.refreshKey, load); onMounted(load)
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <el-alert v-if="syncWarning" :title="syncWarning" type="warning" :closable="false" class="form-alert"><a href="#models">模型设置 →</a></el-alert>
  <input ref="picker" type="file" accept=".json" hidden @change="choose" />
  <div class="section-toolbar"><span>{{ catalog?.items.length ?? 0 }} 个预设</span><div><el-button :loading="busy" @click="picker?.click()">导入预设</el-button><el-button type="primary" :disabled="!catalog" @click="edit()">新建预设</el-button></div></div>
  <section class="panel table-scroll" v-loading="loading"><el-empty v-if="!catalog?.items.length" description="还没有预设" /><el-table v-else :data="catalog.items"><el-table-column prop="name" label="预设" /><el-table-column label="更新时间" min-width="180"><template #default="{ row }">{{ formatTime(row.updated_at, timezone) }}</template></el-table-column><el-table-column min-width="150"><template #default="{ row }"><el-button link @click="edit(row.id)">编辑</el-button><a :href="`/api/v1/models/presets/${row.id}/export?revision=${row.revision}`" download>导出</a></template></el-table-column></el-table></section>
  <EditorWorkspace ref="editor" v-model="visible" :title="editing ? '编辑预设' : draft?.source ? '导入预览' : '新建预设'" :scope="draft?.name" :draft="{ name: draft?.name, config: draft?.config }" :busy="busy">
    <template #heading><el-button type="primary" :loading="busy" :disabled="!draft?.name.trim()" @click="save()">保存预设</el-button></template>
    <el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" />
    <el-form v-if="draft" label-position="top" :disabled="busy" @submit.prevent="save()">
      <el-alert v-if="draft.source?.warnings.length" type="warning" :closable="false" class="form-alert"><ul class="import-warnings"><li v-for="warning in draft.source.warnings" :key="warning">{{ warning }}</li></ul></el-alert>
      <el-form-item label="预设名称"><el-input v-model="draft.name" maxlength="80" /></el-form-item>
      <PresetConfigEditor v-model="draft.config" :templates="catalog?.defaults.context.prompt_blocks.filter(block => !block.enabled)" />
      <el-collapse v-if="draft.source" class="quiet-collapse"><el-collapse-item title="原始导入资料（不发送给模型）" name="source"><pre class="diagnostic-json">{{ pretty(draft.source) }}</pre></el-collapse-item></el-collapse>
      <div class="form-actions editor-actions"><el-button @click="editor?.close()">取消</el-button><el-button v-if="editing" :loading="busy" :disabled="!draft.name.trim()" @click="save(true)">另存为新预设</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!draft.name.trim()">保存预设</el-button></div>
    </el-form>
  </EditorWorkspace>
</template>
