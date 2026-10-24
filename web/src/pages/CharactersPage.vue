<script setup lang="ts">
import EditorWorkspace from '../components/EditorWorkspace.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { Character } from '../roleplay-types'
import type { BookSummary, MaterialImport } from '../material-types'
import ImportDialog from '../components/ImportDialog.vue'
import type { Library } from '../information-types'
const libraries = ref<Library[]>([])

const editor = ref<InstanceType<typeof EditorWorkspace> | null>(null)
const props = defineProps<{ refreshKey: number; timezone: string }>()
const items = ref<Character[]>([]), error = ref(''), loading = ref(false), saving = ref(false), visible = ref(false)
const editingId = ref<string | null>(null)
const books = ref<BookSummary[]>([]), importVisible = ref(false), source = ref<MaterialImport | null>(null)
const empty = () => ({ name: '', description: '', personality: '', scenario: '', dialogue_examples: '', first_message: '', style: '', nickname: '', system_prompt: '', post_history_instructions: '', alternate_greetings: [] as string[], worldbooks: [] as { id: string; revision: number }[], knowledge_libraries: [] as string[], revision: 0 })
const form = reactive(empty())
const selectedBooks = computed({ get: () => form.worldbooks.map(book => book.id), set: (ids: string[]) => { form.worldbooks = ids.map(id => form.worldbooks.find(book => book.id === id) ?? { id, revision: books.value.find(book => book.id === id)!.revision }) } })
const alternatives = computed({ get: () => form.alternate_greetings.join('\n---\n'), set: (text: string) => { form.alternate_greetings = text ? text.split('\n---\n') : [] } })
const editorError = ref(''), section = ref('basic')
let loadVersion = 0
async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const [result, available, knowledge] = await Promise.all([api<Character[]>('/roleplay/characters'), api<BookSummary[]>('/roleplay/worldbooks'), api<Library[]>('/information/libraries')])
    if (version === loadVersion) { items.value = result; books.value = available; libraries.value = knowledge; error.value = '' }
  } catch (cause) { if (version === loadVersion) error.value = errorText(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
function edit(item?: Character) {
  editingId.value = item?.id ?? null
  Object.assign(form, empty())
  if (item) for (const key of Object.keys(empty()) as (keyof ReturnType<typeof empty>)[]) Object.assign(form, { [key]: JSON.parse(JSON.stringify(item[key])) })
  editorError.value = ''; section.value = 'basic'; visible.value = true
}
async function showSource(item: Character) {
  try { source.value = await api<MaterialImport | null>(`/roleplay/characters/${item.id}/source?revision=${item.revision}`); if (source.value) importVisible.value = true; else ElMessage.info('此角色没有导入来源') }
  catch (cause) { ElMessage.error(errorText(cause)) }
}
function importNew() { source.value = null; importVisible.value = true }
async function save() {
  if (saving.value) return
  saving.value = true; editorError.value = ''
  try {
    await api(`/roleplay/characters${editingId.value ? '/' + editingId.value : ''}`, {
      method: editingId.value ? 'PUT' : 'POST', body: JSON.stringify(form),
    })
    visible.value = false; ElMessage.success('角色已保存并同步'); await load()
  } catch (cause) { editorError.value = errorText(cause) }
  finally { saving.value = false }
}
watch(() => props.refreshKey, load)
onMounted(load)
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span>{{ items.length }} 个角色</span><div><el-button @click="importNew">导入角色卡</el-button><el-button type="primary" @click="edit()">新建角色</el-button></div></div>
  <section v-if="!items.length" class="panel" v-loading="loading"><el-empty v-if="!error && !loading" description="还没有角色"><el-button @click="importNew">导入角色卡</el-button></el-empty></section>
  <div class="character-grid">
    <section v-for="item in items" :key="item.id" class="panel character-card">
      <div class="connection-heading"><h2>{{ item.name }}</h2></div>
      <p class="character-description">{{ item.description || item.personality || '尚未填写设定' }}</p>
      <div class="section-toolbar"><time>{{ formatTime(item.updated_at, timezone) }}</time><div><el-button link @click="showSource(item)">来源</el-button><el-button @click="edit(item)">编辑</el-button></div></div>
    </section>
  </div>
  <EditorWorkspace ref="editor" v-model="visible" :title="editingId ? '编辑角色' : '新建角色'" :scope="form.name || undefined" :version="form.revision || null" :draft="form" :busy="saving">
    <template #heading><el-button type="primary" :loading="saving" :disabled="!form.name.trim()" @click="save">保存设定</el-button></template>
    <el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="saving" @submit.prevent="save">
      <el-tabs v-model="section" class="editor-tabs"><el-tab-pane label="基础设定" name="basic">
      <el-form-item label="角色名称"><el-input v-model="form.name" maxlength="80" /></el-form-item>
      <el-form-item label="角色描述"><el-input v-model="form.description" type="textarea" :autosize="{ minRows: 14 }" maxlength="24000" /></el-form-item>
      <el-form-item label="性格"><el-input v-model="form.personality" type="textarea" :autosize="{ minRows: 10 }" maxlength="16000" /></el-form-item>
      </el-tab-pane><el-tab-pane label="表达与提示词" name="expression">
      <el-form-item label="对话风格"><el-input v-model="form.style" type="textarea" :autosize="{ minRows: 12 }" maxlength="4000" /></el-form-item>
      <el-collapse class="quiet-collapse"><el-collapse-item title="角色称呼与提示词覆盖" name="prompts">
        <el-form-item label="宏替换使用的角色称呼"><el-input v-model="form.nickname" maxlength="80" /></el-form-item>
        <el-form-item label="角色系统提示词"><el-input v-model="form.system_prompt" type="textarea" :autosize="{ minRows: 14 }" maxlength="8000" placeholder="留空继承当前模型预设；{{original}} 引用继承内容" /></el-form-item>
        <el-form-item label="历史后提示词"><el-input v-model="form.post_history_instructions" type="textarea" :autosize="{ minRows: 10 }" maxlength="8000" placeholder="留空继承当前模型预设" /></el-form-item>
      </el-collapse-item></el-collapse>
      </el-tab-pane><el-tab-pane label="场景与示例" name="examples">
        <el-form-item label="场景"><el-input v-model="form.scenario" type="textarea" :autosize="{ minRows: 10 }" maxlength="16000" /></el-form-item>
        <el-form-item label="对话示例"><el-input v-model="form.dialogue_examples" type="textarea" :autosize="{ minRows: 16 }" maxlength="24000" /></el-form-item>
        <el-form-item label="开场白设定"><el-input v-model="form.first_message" type="textarea" :autosize="{ minRows: 8 }" maxlength="8000" /></el-form-item>
        <el-form-item label="备用开场白（独占一行的 --- 分隔）"><el-input v-model="alternatives" type="textarea" :autosize="{ minRows: 8 }" /></el-form-item>
      </el-tab-pane><el-tab-pane label="关联资料" name="materials">
      <el-form-item label="关联世界书"><el-select v-model="selectedBooks" multiple :multiple-limit="8" clearable><el-option v-for="book in books" :key="book.id" :value="book.id" :label="book.name" /></el-select></el-form-item>
      <el-form-item label="授权资料库"><el-select v-model="form.knowledge_libraries" multiple :multiple-limit="8" clearable><el-option v-for="library in libraries" :key="library.id" :value="library.id" :label="library.name + (library.enabled ? '' : '（停用）')" /></el-select></el-form-item>
      <div v-if="form.worldbooks.length" class="section-toolbar"><span>{{ form.worldbooks.map(ref => `${books.find(book => book.id === ref.id)?.name ?? ref.id}`).join('、') }}</span></div>
      </el-tab-pane></el-tabs>
      <div class="form-actions editor-actions"><el-button @click="editor?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!form.name.trim()">保存设定</el-button></div>
    </el-form>
  </EditorWorkspace>
  <ImportDialog v-model="importVisible" kind="character" :source="source" @committed="load" />
</template>
