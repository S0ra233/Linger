<script setup lang="ts">
import EditDialog from '../components/EditDialog.vue'
import EditorWorkspace from '../components/EditorWorkspace.vue'
import ConversationWindowLink from '../components/ConversationWindowLink.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { selectedLifeLine } from '../ui-state'
import { api, errorText, formatTime } from '../api'
import type { CursorPage, InformationResults, KnowledgeDocument, Library } from '../information-types'
import type { RoleInstance } from '../roleplay-types'
const editor = ref<InstanceType<typeof EditorWorkspace> | null>(null)
const libraryDialog = ref<InstanceType<typeof EditDialog> | null>(null)
const props = defineProps<{ refreshKey: number; timezone: string }>()
const libraries = ref<Library[]>([]), selected = ref(''), documents = ref<KnowledgeDocument[]>([]), next = ref<string | null>(null), tab = ref('documents')
const updates = ref(false)
const error = ref(''), editorError = ref(''), loading = ref(false), busy = ref(false), libraryVisible = ref(false), documentVisible = ref(false)
const editingLibrary = ref<string | null>(null), editingDocument = ref<string | null>(null), documentLibrary = ref('')
const libraryForm = reactive({ name: '', enabled: true, revision: 0 })
const documentForm = reactive({ title: '', text: '', enabled: true, revision: 0 }), versions = ref<KnowledgeDocument['versions']>([])
const picker = ref<HTMLInputElement | null>(null), encoding = ref('utf-8-sig'), uploadLibrary = ref('')
const instances = ref<RoleInstance[]>([]), searchInstance = selectedLifeLine, query = ref(''), searchMode = ref('keyword'), category = ref('all'), searching = ref(false), searchResult = ref<InformationResults | null>(null)
const libraryName = computed(() => libraries.value.find(item => item.id === documentLibrary.value)?.name ?? '资料库')
let loadVersion = 0, documentVersion = 0, editVersion = 0, searchVersion = 0
async function load() {
  const mine = ++loadVersion
  try { const [items, rows] = await Promise.all([api<Library[]>('/information/libraries'), api<RoleInstance[]>('/roleplay/instances')]); if (mine === loadVersion) { libraries.value = items; instances.value = rows; error.value = '' } }
  catch (cause) { if (mine === loadVersion) error.value = errorText(cause) }
}
async function loadDocuments(more = false) {
  if (!selected.value) return
  const id = selected.value, mine = ++documentVersion; loading.value = true; if (!more) updates.value = false
  try { const page = await api<CursorPage<KnowledgeDocument>>(`/information/libraries/${id}/documents${more && next.value ? '?before=' + next.value : ''}`); if (mine === documentVersion) { documents.value = more ? [...documents.value, ...page.items] : page.items; next.value = page.next_before; error.value = '' } }
  catch (cause) { if (mine === documentVersion) error.value = errorText(cause) }
  finally { if (mine === documentVersion) loading.value = false }
}
function editLibrary(item?: Library) { editingLibrary.value = item?.id ?? null; Object.assign(libraryForm, { name: item?.name ?? '', enabled: item ? !!item.enabled : true, revision: item?.revision ?? 0 }); editorError.value = ''; libraryVisible.value = true }
async function saveLibrary() {
  if (busy.value) return
  busy.value = true; editorError.value = ''
  try { const value = await api<Library>(`/information/libraries${editingLibrary.value ? '/' + editingLibrary.value : ''}`, { method: editingLibrary.value ? 'PUT' : 'POST', body: JSON.stringify(libraryForm) }); libraryVisible.value = false; selected.value = value.id; await load(); ElMessage.success('资料库已保存') }
  catch (cause) { editorError.value = errorText(cause) }
  finally { busy.value = false }
}
async function editDocument(item?: KnowledgeDocument) {
  const mine = ++editVersion; editingDocument.value = item?.id ?? null; documentLibrary.value = selected.value; versions.value = []; editorError.value = ''
  Object.assign(documentForm, { title: '', text: '', enabled: true, revision: 0 })
  if (item) {
    try { const value = await api<KnowledgeDocument>(`/information/libraries/${documentLibrary.value}/documents/${item.id}`); if (mine !== editVersion) return; Object.assign(documentForm, { title: value.title, text: value.text ?? '', enabled: !!value.enabled, revision: value.revision }); versions.value = value.versions ?? [] }
    catch (cause) { error.value = errorText(cause); return }
  }
  documentVisible.value = true
}
async function saveDocument() {
  if (busy.value) return
  busy.value = true; editorError.value = ''
  try { await api(`/information/libraries/${documentLibrary.value}/documents${editingDocument.value ? '/' + editingDocument.value : ''}`, { method: editingDocument.value ? 'PUT' : 'POST', body: JSON.stringify(documentForm) }); documentVisible.value = false; ElMessage.success('资料与关键词索引已保存'); if (selected.value === documentLibrary.value) await loadDocuments(); await load() }
  catch (cause) { editorError.value = errorText(cause) }
  finally { busy.value = false }
}
function selectFile() { uploadLibrary.value = selected.value; picker.value?.click() }
async function importFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0], id = uploadLibrary.value
  if (!file || !id) return
  if (file.size > 512000) { error.value = '单篇资料不能超过 512000 字节。'; return }
  busy.value = true
  try { await api(`/information/libraries/${id}/documents/import?filename=${encodeURIComponent(file.name.slice(0, 200))}&encoding=${encoding.value}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } }, 60000); ElMessage.success('资料已导入'); if (id === selected.value) await loadDocuments(); await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false; if (picker.value) picker.value.value = '' }
}
async function search() {
  if (!searchInstance.value || !query.value.trim() || searching.value) return
  const mine = ++searchVersion; searching.value = true; error.value = ''; searchResult.value = null
  try { const value = await api<InformationResults>(`/information/instances/${searchInstance.value}/search`, { method: 'POST', body: JSON.stringify({ query: query.value, category: category.value, top_k: 8, mode: searchMode.value }) }); if (mine === searchVersion) searchResult.value = value }
  catch (cause) { if (mine === searchVersion) error.value = errorText(cause) }
  finally { if (mine === searchVersion) searching.value = false }
}
watch(selected, () => { documentVersion++; documents.value = []; next.value = null; void loadDocuments() })
watch([searchInstance, query, category, searchMode], () => { searchVersion++; searchResult.value = null; searching.value = false })
watch(() => props.refreshKey, () => { updates.value = true; void load() }); onMounted(load)
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <el-tabs v-model="tab" class="management-tabs"><el-tab-pane label="资料库" name="documents">
    <div class="section-toolbar"><el-select v-model="selected" placeholder="选择资料库" class="account-select"><el-option v-for="item in libraries" :key="item.id" :value="item.id" :label="`${item.name}${item.enabled ? '' : '（停用）'}`" /></el-select><div><el-button v-if="selected" @click="editLibrary(libraries.find(item => item.id === selected))">编辑资料库</el-button><el-button type="primary" @click="editLibrary()">新建资料库</el-button></div></div>
    <section class="panel table-scroll" v-loading="loading"><div class="section-toolbar"><span>{{ documents.length }} 篇已载入</span><div><el-button :type="updates ? 'primary' : 'default'" :disabled="!selected" @click="loadDocuments()">{{ updates ? '有变化 · 刷新' : '刷新' }}</el-button><el-button :disabled="!selected || busy" @click="selectFile">导入 TXT／MD</el-button><el-button :disabled="!selected || busy" @click="editDocument()">新建文档</el-button></div></div>
      <input ref="picker" type="file" accept=".txt,.md,.markdown" hidden @change="importFile" /><el-collapse class="quiet-collapse"><el-collapse-item title="导入编码" name="encoding"><el-select v-model="encoding" class="account-select"><el-option value="utf-8-sig" label="UTF-8" /><el-option value="utf-16" label="UTF-16（含 BOM）" /><el-option value="gb18030" label="GB18030／GBK" /></el-select></el-collapse-item></el-collapse>
      <el-empty v-if="!documents.length" :description="selected ? '还没有文档' : '先选择资料库'" /><el-table v-else :data="documents"><el-table-column prop="title" label="文档" min-width="200" /><el-table-column label="状态" width="90"><template #default="{ row }">{{ row.enabled ? '启用' : '停用' }}</template></el-table-column><el-table-column label="版本" width="80"><template #default="{ row }">v{{ row.revision }}</template></el-table-column><el-table-column prop="characters" label="字符数" width="100" /><el-table-column label="更新" min-width="180"><template #default="{ row }">{{ formatTime(row.updated_at, timezone) }}</template></el-table-column><el-table-column width="90"><template #default="{ row }"><el-button link @click="editDocument(row)">编辑</el-button></template></el-table-column></el-table><div v-if="next" class="load-more"><el-button :loading="loading" @click="loadDocuments(true)">载入更多</el-button></div>
    </section>
  </el-tab-pane><el-tab-pane label="检索预览" name="search"><section class="panel"><el-form label-position="top" :disabled="searching" @submit.prevent="search"><el-form-item label="生活线授权范围"><el-select v-model="searchInstance" filterable><el-option v-for="item in instances" :key="item.id" :value="item.id" :label="`${item.display_name || item.nickname || item.peer_id} · ${item.character_name} · ${item.account_name}`" /></el-select></el-form-item><div class="form-two-columns"><el-form-item label="检索内容"><el-input v-model="query" maxlength="500" /></el-form-item><el-form-item label="类型"><el-select v-model="category"><el-option value="all" label="记忆与知识" /><el-option value="memory" label="私有记忆" /><el-option value="knowledge" label="授权资料" /></el-select></el-form-item></div><el-form-item label="检索方式"><el-select v-model="searchMode"><el-option value="keyword" label="关键词" /><el-option value="semantic" label="语义（调用 Embedding）" /><el-option value="hybrid" label="混合（调用 Embedding）" /></el-select></el-form-item><el-alert v-if="searchMode !== 'keyword'" title="本次检索可能产生 Embedding 费用；未建立索引时请先到语义索引页面处理。" type="info" :closable="false" class="form-alert" /><el-button type="primary" native-type="submit" :loading="searching" :disabled="!searchInstance || !query.trim()">{{ searchMode === 'keyword' ? '关键词检索' : searchMode === 'semantic' ? '语义检索' : '混合检索' }}</el-button></el-form><template v-if="searchResult"><el-alert v-if="searchResult.fallback" :title="'已回退关键词：' + searchResult.fallback" type="warning" :closable="false" class="form-alert" /><p class="check-metadata">{{ searchResult.score_basis.startsWith('RRF') ? '混合排序' : searchResult.score_basis.startsWith('cosine') ? '语义相似度排序' : '关键词排序' }} · 分数不代表真实性</p><details class="raw-details"><summary>索引覆盖详情</summary><p v-for="(scope, index) in searchResult.coverage ?? []" :key="index" class="check-metadata">有效片段 {{ scope.chunks }} · 已索引 {{ scope.indexed }} · 本次扫描 {{ scope.scanned_vectors }}{{ scope.index_complete ? '' : ' · 索引尚未齐全' }}</p></details><el-alert v-if="!searchResult.scan_complete" title="已达到本次倒排扫描上限，结果不是全量排名。" type="warning" :closable="false" class="form-alert" /><el-empty v-if="!searchResult.results.length" description="没有命中有效资料" /><article v-for="item in searchResult.results" :key="item.chunk_id" class="check-record"><div class="section-toolbar"><h3>{{ item.title }}</h3><el-tag effect="plain">{{ item.memory_id ? '私有记忆' : '授权资料' }} · v{{ item.revision }}</el-tag></div><p class="message-text">{{ item.text }}</p><details v-if="item.conversation_anchors?.length" class="raw-details"><summary>原对话来源</summary><div class="inline-actions"><ConversationWindowLink v-for="source in item.conversation_anchors" :key="`${source.kind}:${source.id}`" :instance-id="searchInstance" :source="source" :timezone="timezone" :label="`${source.kind === 'input' ? '用户输入' : '已送达'} #${source.id} 附近`" /></div><p v-if="item.anchors_limited" class="muted">仅列出最近 8 个有效对话来源</p></details><div class="check-metadata"><span>行 {{ item.start_line }}—{{ item.end_line }}</span><span>排序分 {{ item.score }}</span></div></article></template></section></el-tab-pane></el-tabs>
  <EditDialog ref="libraryDialog" :draft="libraryForm" :busy="busy" v-model="libraryVisible" :title="editingLibrary ? '编辑资料库' : '新建资料库'" width="500px" class="model-dialog" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy"><el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" /><el-form label-position="top" :disabled="busy" @submit.prevent="saveLibrary"><el-form-item label="名称"><el-input v-model="libraryForm.name" maxlength="80" /></el-form-item><el-form-item label="启用"><el-switch v-model="libraryForm.enabled" /></el-form-item><div class="form-actions"><el-button @click="libraryDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!libraryForm.name.trim()">保存</el-button></div></el-form></EditDialog>
  <EditorWorkspace ref="editor" v-model="documentVisible" :title="editingDocument ? '编辑文档' : '新建文档'" :scope="libraryName + ' · 保存文档版本'" :version="documentForm.revision || null" :draft="documentForm" :busy="busy"><el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" /><el-form label-position="top" :disabled="busy" @submit.prevent="saveDocument"><el-form-item label="标题"><el-input v-model="documentForm.title" maxlength="200" /></el-form-item><el-form-item label="正文"><el-input v-model="documentForm.text" type="textarea" :autosize="{ minRows: 20 }" maxlength="250000" /></el-form-item><el-form-item label="参与检索"><el-switch v-model="documentForm.enabled" /></el-form-item><el-collapse v-if="editingDocument" class="quiet-collapse"><el-collapse-item title="历史版本与原文" name="versions"><div v-for="item in versions" :key="item.revision" class="section-toolbar"><span>v{{ item.revision }} · {{ item.title }} · {{ item.encoding }}</span><a :href="`/api/v1/information/libraries/${documentLibrary}/documents/${editingDocument}/original?revision=${item.revision}`" download>下载原文</a></div></el-collapse-item></el-collapse><div class="form-actions editor-actions"><el-button @click="editor?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!documentForm.title.trim() || !documentForm.text.trim()">保存版本</el-button></div></el-form></EditorWorkspace>
</template>
