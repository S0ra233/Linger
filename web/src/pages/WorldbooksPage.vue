<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { BookSummary, MaterialImport, Worldbook, WorldEntry } from '../material-types'
import EditorWorkspace from '../components/EditorWorkspace.vue'
import SplitPane from '../components/SplitPane.vue'
import KeywordInput from '../components/KeywordInput.vue'
import ImportDialog from '../components/ImportDialog.vue'

const props = defineProps<{ refreshKey: number; timezone: string }>()
const items = ref<BookSummary[]>([]), error = ref(''), editorError = ref(''), saving = ref(false), visible = ref(false), loading = ref(false)
const importVisible = ref(false), source = ref<MaterialImport | null>(null), editingId = ref<string | null>(null), sourceId = ref<string | null>(null)
const empty = (): Worldbook => ({ name: '', description: '', scan_depth: 20, token_budget: 2048, entries: [], revision: 0 })
const form = reactive(empty())
const editor = ref<InstanceType<typeof EditorWorkspace> | null>(null)
const entryIndex = ref(0), query = ref(''), entryFilter = ref('all')
const blankEntry = (): WorldEntry => ({ id: crypto.randomUUID(), name: '', content: '', keys: [], secondary_keys: [], selective_logic: 'none', constant: false, enabled: true, case_sensitive: false, insertion_order: 100, priority: 100, position: 'after_char', run_types: [], sticky: 0, cooldown: 0, delay: 0 })
const entry = computed(() => form.entries[entryIndex.value])
const displayed = computed(() => form.entries.map((item, index) => ({ item, index })).filter(({ item }) =>
  (entryFilter.value === 'all' || item.enabled === (entryFilter.value === 'enabled')) &&
  `${item.name} ${item.content} ${item.keys.join(' ')}`.toLocaleLowerCase().includes(query.value.toLocaleLowerCase())))
function addEntry() { form.entries.push(blankEntry()); entryIndex.value = form.entries.length - 1; query.value = ''; entryFilter.value = 'all' }
function removeEntry() { form.entries.splice(entryIndex.value, 1); entryIndex.value = Math.max(0, Math.min(entryIndex.value, form.entries.length - 1)) }
const logic = [{ value: 'none', label: '不附加条件' }, { value: 'and_any', label: '至少命中一个' }, { value: 'and_all', label: '全部命中' }, { value: 'not_any', label: '全部不命中' }, { value: 'not_all', label: '至少一个不命中' }]
let version = 0, editVersion = 0
async function load() {
  const mine = ++version; loading.value = true
  try { const value = await api<BookSummary[]>('/roleplay/worldbooks'); if (mine === version) { items.value = value; error.value = '' } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function edit(item?: BookSummary) {
  const mine = ++editVersion; editorError.value = ''; editingId.value = item?.id ?? null; sourceId.value = null; Object.assign(form, empty())
  if (item) {
    try { const value = await api<Worldbook>(`/roleplay/worldbooks/${item.id}`); if (mine !== editVersion) return; sourceId.value = value.import_id ?? null; for (const key of Object.keys(empty())) Object.assign(form, { [key]: value[key as keyof Worldbook] }) }
    catch (cause) { if (mine === editVersion) error.value = errorText(cause); return }
  }
  entryIndex.value = 0; query.value = ''; entryFilter.value = 'all'; visible.value = true
}
async function save() {
  if (saving.value) return
  saving.value = true; editorError.value = ''
  try { await api(`/roleplay/worldbooks${editingId.value ? '/' + editingId.value : ''}`, { method: editingId.value ? 'PUT' : 'POST', body: JSON.stringify(form) }); visible.value = false; ElMessage.success('世界书已保存并同步'); await load() }
  catch (cause) { editorError.value = errorText(cause) }
  finally { saving.value = false }
}
async function original() {
  if (!sourceId.value) return
  try { source.value = await api<MaterialImport>(`/roleplay/imports/${sourceId.value}`); importVisible.value = true }
  catch (cause) { editorError.value = errorText(cause) }
}
watch(() => props.refreshKey, load); onMounted(load)
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span>{{ items.length }} 本世界书</span><div><el-button @click="source = null; importVisible = true">导入世界书</el-button><el-button type="primary" @click="edit()">新建世界书</el-button></div></div>
  <section class="panel table-scroll" v-loading="loading"><el-empty v-if="!items.length" description="还没有世界书" /><el-table v-else :data="items"><el-table-column prop="name" label="名称" /><el-table-column prop="entry_count" label="条目" width="90" /><el-table-column label="更新时间" min-width="180"><template #default="{ row }">{{ formatTime(row.updated_at, timezone) }}</template></el-table-column><el-table-column width="90"><template #default="{ row }"><el-button link @click="edit(row)">编辑</el-button></template></el-table-column></el-table></section>
  <EditorWorkspace ref="editor" v-model="visible" :title="editingId ? '编辑世界书' : '新建世界书'" :scope="form.name || undefined" :version="form.revision || null" :draft="form" :busy="saving">
    <template #heading><el-button type="primary" :loading="saving" :disabled="!form.name.trim()" @click="save">保存世界书</el-button></template>
    <el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="saving" @submit.prevent="save">
      <div class="form-two-columns"><el-form-item label="世界书名称"><el-input v-model="form.name" maxlength="80" /></el-form-item><div class="form-two-columns"><el-form-item label="扫描消息数"><el-input-number v-model="form.scan_depth" :min="0" :max="100" /></el-form-item><el-form-item label="本书预算 tokens"><el-input-number v-model="form.token_budget" :min="0" :max="32000" /></el-form-item></div></div>
      <el-collapse class="quiet-collapse"><el-collapse-item title="资料备注" name="description"><el-input v-model="form.description" type="textarea" :autosize="{ minRows: 6 }" maxlength="4000" /></el-collapse-item></el-collapse>
      <SplitPane label="调整世界书条目列表宽度" :selection="entryIndex" :detail-label="entry?.name || '世界书条目'"><template #list="{ open: openDetail }">
        <div class="section-toolbar"><strong>{{ form.entries.length }} 个条目</strong><el-button :disabled="form.entries.length >= 500" @click="addEntry(); openDetail()">添加</el-button></div>
        <el-input v-model="query" placeholder="搜索条目" clearable aria-label="搜索世界书条目" />
        <el-select v-model="entryFilter" aria-label="筛选条目状态" class="section-gap"><el-option value="all" label="全部条目" /><el-option value="enabled" label="已启用" /><el-option value="disabled" label="已停用" /></el-select>
        <div class="editor-list"><button v-for="row in displayed" :key="row.item.id" type="button" :class="['editor-list-item', { active: entryIndex === row.index }]" @click="entryIndex = row.index; openDetail()"><strong>{{ row.item.name || row.item.content.slice(0, 30) || '新条目' }}</strong><span>{{ row.item.enabled ? row.item.constant ? '常驻' : '关键词触发' : '已停用' }} · 顺序 {{ row.item.insertion_order }}</span></button><el-empty v-if="!displayed.length" description="没有匹配条目" :image-size="55" /></div>
      </template>
      <template v-if="entry">
        <div class="section-toolbar"><el-switch v-model="entry.enabled" active-text="启用条目" /><el-button link type="danger" @click="removeEntry">移除条目</el-button></div>
        <el-form-item label="管理名称"><el-input v-model="entry.name" maxlength="200" /></el-form-item>
        <el-form-item label="适用运行（不选则全部）"><el-checkbox-group v-model="entry.run_types"><el-checkbox value="input">普通回复</el-checkbox><el-checkbox value="life">后台生活</el-checkbox></el-checkbox-group></el-form-item>
        <el-form-item label="正文"><el-input v-model="entry.content" type="textarea" :autosize="{ minRows: 18 }" maxlength="16000" /></el-form-item>
        <el-collapse class="quiet-collapse"><el-collapse-item title="条目时效" name="timing"><div class="form-two-columns"><el-form-item label="持续轮数（含触发轮）"><el-input-number v-model="entry.sticky" :min="0" :max="1000" /></el-form-item><el-form-item label="持续结束后冷却轮数"><el-input-number v-model="entry.cooldown" :min="0" :max="1000" /></el-form-item><el-form-item label="本版本延迟启用轮数"><el-input-number v-model="entry.delay" :min="0" :max="1000" /></el-form-item></div><p>按本生活线有效 RP 提交计数；0 为关闭。</p></el-collapse-item></el-collapse>
        <el-form-item label="触发方式"><el-radio-group :model-value="entry.constant ? 'constant' : 'keyword'" @update:model-value="(value: string | number | boolean) => { if (entry) entry.constant = value === 'constant' }"><el-radio-button value="keyword">关键词</el-radio-button><el-radio-button value="constant">常驻</el-radio-button></el-radio-group></el-form-item>
        <template v-if="!entry.constant"><el-form-item label="关键词（命中任一）"><KeywordInput v-model="entry.keys" /></el-form-item><el-collapse class="quiet-collapse"><el-collapse-item title="次级条件" name="secondary"><el-form-item label="次级关键词"><KeywordInput v-model="entry.secondary_keys" label="次级关键词" /></el-form-item><el-form-item label="次级逻辑"><el-select v-model="entry.selective_logic"><el-option v-for="item in logic" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-collapse-item></el-collapse></template>
        <el-collapse class="quiet-collapse"><el-collapse-item title="插入规则" name="rules"><div class="form-two-columns"><el-form-item label="插入位置"><el-select v-model="entry.position"><el-option value="before_char" label="角色资料前" /><el-option value="after_char" label="角色资料后" /></el-select></el-form-item><el-form-item label="区分大小写"><el-switch v-model="entry.case_sensitive" /></el-form-item><el-form-item label="插入顺序（小值在前）"><el-input-number v-model="entry.insertion_order" :min="-100000" :max="100000" /></el-form-item><el-form-item label="预算优先级（大值优先）"><el-input-number v-model="entry.priority" :min="-100000" :max="100000" /></el-form-item></div></el-collapse-item></el-collapse>
      </template><el-empty v-else description="添加条目开始编辑" />
      </SplitPane>
      <div class="form-actions editor-actions"><el-button v-if="sourceId" link @click="original">导入来源</el-button><span class="action-spacer"></span><el-button @click="editor?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!form.name.trim()">保存世界书</el-button></div>
    </el-form>
  </EditorWorkspace>
  <ImportDialog v-model="importVisible" kind="worldbook" :source="source" @committed="load" />
</template>
