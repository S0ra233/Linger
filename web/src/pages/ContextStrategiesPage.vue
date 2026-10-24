<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText } from '../api'
import { selectedLifeLine } from '../ui-state'
import { conversationLabel, strategyModes } from '../strategy-types'
import type { ContextStrategy, StrategyCatalog, StrategyRules, BoundConversation } from '../strategy-types'
import ContextRulesEditor from '../components/ContextRulesEditor.vue'
import ContextBindingPanel from '../components/ContextBindingPanel.vue'
import ContextPage from './ContextPage.vue'
import EditorWorkspace from '../components/EditorWorkspace.vue'
import EditDialog from '../components/EditDialog.vue'
const props = defineProps<{ refreshKey: number; timezone: string }>()
const catalog = ref<StrategyCatalog | null>(null), conversations = ref<BoundConversation[]>([])
const error = ref(''), editError = ref(''), bindError = ref(''), loading = ref(false), busy = ref(false), tab = ref(new URLSearchParams(window.location.hash.split('?')[1] ?? '').has('instance') ? 'effects' : 'library')
const search = ref(''), selected = selectedLifeLine
const filtered = computed(() => conversations.value.filter(item => !search.value || conversationLabel(item).toLocaleLowerCase().includes(search.value.toLocaleLowerCase())))
const open = ref(false), editor = ref<InstanceType<typeof EditorWorkspace> | null>(null), draft = ref<StrategyRules | null>(null), name = ref('')
const bindOpen = ref(false), bindDialog = ref<InstanceType<typeof EditDialog> | null>(null), bindStrategy = ref<ContextStrategy | null>(null), targets = ref<string[]>([])
let editing: ContextStrategy | null = null, version = 0, bindingRevisions: Record<string, number> = {}
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
async function load() {
  const mine = ++version; loading.value = true
  try { const [values, rows] = await Promise.all([api<StrategyCatalog>('/context-strategies'), api<BoundConversation[]>('/context-strategies/bindings')]); if (mine === version) { catalog.value = values; conversations.value = rows; error.value = '' } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function edit(item?: ContextStrategy, mode: 'cost' | 'continuity' = 'cost', copy = false) {
  if (!catalog.value) return
  editing = item && !copy ? clone(item) : null
  name.value = item ? item.name + (copy ? ' 副本' : '') : ''
  draft.value = clone(item?.config ?? catalog.value.defaults[mode]); editError.value = ''; open.value = true
}
async function save() {
  if (!draft.value || busy.value) return
  busy.value = true; editError.value = ''
  try { await api(`/context-strategies${editing ? '/' + editing.id : ''}`, { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ name: name.value, config: draft.value, revision: editing?.revision ?? 0 }) }); open.value = false; ElMessage.success('策略已保存，已有对话的规则保留'); await load() }
  catch (cause) { editError.value = errorText(cause) }
  finally { busy.value = false }
}
function bind(item: ContextStrategy) { bindStrategy.value = clone(item); bindingRevisions = Object.fromEntries(conversations.value.map(row => [row.id, row.binding_revision])); targets.value = []; bindError.value = ''; bindOpen.value = true }
async function saveBindings() {
  if (!bindStrategy.value || busy.value || !targets.value.length) return
  const rows = targets.value.map(id => ({ id, revision: bindingRevisions[id] }))
  if (rows.some(item => item.revision === undefined)) { bindError.value = '对话列表已变化，请关闭弹窗后重新绑定。'; return }
  busy.value = true; bindError.value = ''
  try { await api('/context-strategies/bindings', { method: 'PUT', body: JSON.stringify({ strategy_id: bindStrategy.value.id, strategy_revision: bindStrategy.value.revision, targets: rows.map(item => ({ instance_id: item.id, revision: item.revision, overrides: {} })) }) }); bindOpen.value = false; ElMessage.success('绑定已保存，下次运行采用'); await load() }
  catch (cause) { bindError.value = errorText(cause) }
  finally { busy.value = false }
}
watch(() => props.refreshKey, load); onMounted(load)
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <el-tabs v-model="tab" class="management-tabs"><el-tab-pane label="策略库" name="library">
    <div class="section-toolbar"><span>{{ catalog?.items.length ?? 0 }} 个策略</span><el-dropdown trigger="click" @command="(mode: 'cost' | 'continuity') => edit(undefined, mode)"><el-button type="primary" :disabled="!catalog">新建策略 ⌄</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="cost">成本优先</el-dropdown-item><el-dropdown-item command="continuity">对话连续性优先</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div>
    <section class="panel table-scroll" v-loading="loading"><el-empty v-if="!catalog?.items.length" description="先设计规则，再绑定对话。无需API连接。"><el-button @click="edit()" :disabled="!catalog">新建成本策略</el-button></el-empty><el-table v-else :data="catalog.items"><el-table-column prop="name" label="策略" min-width="150" /><el-table-column label="模式" min-width="165"><template #default="{ row }">{{ strategyModes[row.config.strategy] }}</template></el-table-column><el-table-column label="移出规则" min-width="150"><template #default="{ row }">{{ row.config.history_trigger_turns }} → {{ row.config.retain_turns }} 轮</template></el-table-column><el-table-column label="绑定对话" width="100" prop="bound_count" /><el-table-column label="操作" min-width="210"><template #default="{ row }"><el-button link @click="edit(row)">编辑</el-button><el-button link @click="bind(row)">绑定对话</el-button><el-button link @click="edit(row, 'cost', true)">复制</el-button></template></el-table-column></el-table></section>
  </el-tab-pane><el-tab-pane label="对话绑定" name="bindings"><div class="section-toolbar"><el-input v-model="search" placeholder="搜索联系人、角色或账号" clearable class="connection-search" /></div><section class="panel table-scroll"><el-table :data="filtered" empty-text="尚无对话，可先在联系人页绑定角色"><el-table-column label="对话" min-width="250"><template #default="{ row }">{{ conversationLabel(row) }}</template></el-table-column><el-table-column label="采用策略" min-width="180"><template #default="{ row }">{{ row.strategy_name || '兼容配置' }}</template></el-table-column><el-table-column label="单独调整" width="100"><template #default="{ row }">{{ row.overridden ? '有' : '无' }}</template></el-table-column><el-table-column width="100"><template #default="{ row }"><el-button link @click="selected = row.id; tab = 'effects'">管理</el-button></template></el-table-column></el-table></section>
  </el-tab-pane><el-tab-pane label="占用与效果" name="effects"><div class="section-toolbar"><el-select v-model="selected" filterable placeholder="选择对话" class="account-select"><el-option v-for="item in conversations" :key="item.id" :value="item.id" :label="conversationLabel(item)" /></el-select></div><el-empty v-if="!selected" description="绑定对话后，可查看采用规则与上下文占用" /><template v-else><ContextBindingPanel :instance-id="selected" :refresh-key="refreshKey" @saved="load" /><ContextPage :refresh-key="refreshKey" :timezone="timezone" embedded :show-binding="false" /></template></el-tab-pane></el-tabs>
  <EditorWorkspace ref="editor" v-model="open" title="上下文策略" :scope="editing ? '保存策略 · 已绑定对话可手动同步' : '独立策略 · 可供多个对话选用'" :version="editing?.revision" :draft="{ name, draft }" :busy="busy"><el-alert v-if="editError" :title="editError" type="error" :closable="false" class="form-alert" /><el-form v-if="draft" label-position="top" :disabled="busy" @submit.prevent="save"><el-form-item label="策略名称"><el-input v-model="name" maxlength="80" /></el-form-item><ContextRulesEditor v-model="draft" /><div class="form-actions"><el-button @click="editor?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!name.trim()">保存策略</el-button></div></el-form></EditorWorkspace>
  <EditDialog ref="bindDialog" v-model="bindOpen" title="绑定对话" width="650px" :draft="targets" :busy="busy"><el-alert v-if="bindError" :title="bindError" type="error" :closable="false" class="form-alert" /><el-form label-position="top" :disabled="busy" @submit.prevent="saveBindings"><p class="section-gap">{{ bindStrategy?.name }}</p><el-form-item label="选择对话"><el-select v-model="targets" multiple filterable class="full-width"><el-option v-for="item in conversations" :key="item.id" :value="item.id" :label="`${conversationLabel(item)}${item.strategy_name ? ' · 当前 ' + item.strategy_name : ''}`" /></el-select></el-form-item><p class="field-hint">替换所选对话的绑定并清除其单独参数；聊天和工作集保留。</p><div class="form-actions"><el-button @click="bindDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!targets.length">采用策略</el-button></div></el-form></EditDialog>
</template>
