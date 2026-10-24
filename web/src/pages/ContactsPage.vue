<script setup lang="ts">
import EditDialog from '../components/EditDialog.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import EditorWorkspace from '../components/EditorWorkspace.vue'
import MemoriesPage from './MemoriesPage.vue'
import ContextPage from './ContextPage.vue'
import RuntimePage from './RuntimePage.vue'
import McpPermissions from '../components/McpPermissions.vue'
import EvidencePanel from '../components/EvidencePanel.vue'
import CharacterAdjustments from '../components/CharacterAdjustments.vue'
import LifeMaterials from '../components/LifeMaterials.vue'
import LifePage from './LifePage.vue'
import ConversationHistory from '../components/ConversationHistory.vue'
import ClearContextButton from '../components/ClearContextButton.vue'
import { mayLeave, selectedLifeLine } from '../ui-state'
import { api, errorText, formatTime } from '../api'
import type { Character, Contact, InputMessage, InputMessagesPage, QQAccount, QQAccountsResponse, RoleInstance } from '../roleplay-types'

const bindingDialog = ref<InstanceType<typeof EditDialog> | null>(null)
const props = defineProps<{ refreshKey: number; timezone: string }>()
const detailTab = ref('conversation')
const advancedTabs: Record<string, string> = { evidence: '原始来源', adjustments: '角色调整', mcp: '外部工具', runtime: '状态与投递' }
async function openAdvanced(value: string) { if (advancedTabs[value] && await mayLeave()) detailTab.value = value }
const detailEditor = ref<InstanceType<typeof EditorWorkspace> | null>(null)
const accounts = ref<QQAccount[]>([]), characters = ref<Character[]>([]), contacts = ref<Contact[]>([]), instances = ref<RoleInstance[]>([])
const search = ref('')
const match = (value: unknown) => String(value ?? '').toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())
const visibleContacts = computed(() => contacts.value.filter(item => [item.display_name, item.nickname, item.peer_id, item.character_name].some(match)))
const accountId = ref(''), tab = ref('contacts'), error = ref(''), loading = ref(false)
const bindingVisible = ref(false), saving = ref(false), bindingError = ref(''), editingPeer = ref(false)
const form = reactive({ account_id: '', peer_id: '', character_id: '', display_name: '', enabled: true, revision: 0 })
const history = computed(() => instances.value.filter(item => item.account_id === accountId.value && [item.display_name, item.nickname, item.peer_id, item.character_name].some(match)))
const messageVisible = ref(false), selectedInstance = ref<RoleInstance | null>(null), messages = ref<InputMessage[]>([])
const messageError = ref(''), messageLoading = ref(false), nextBefore = ref<number | null>(null), messageUpdates = ref(false)
const busyImage = ref('')
const conversationKey = ref(0)
let openedRequest = ''
function openRequested() {
  const id = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('instance')
  if (!id || id === openedRequest) return
  const instance = instances.value.find(item => item.id === id)
  if (instance) { openedRequest = id; accountId.value = instance.account_id; openMessages(instance) }
}
let loadVersion = 0, contactVersion = 0, messageVersion = 0

async function loadContacts() {
  const version = ++contactVersion, target = accountId.value
  if (!target) { contacts.value = []; return }
  try {
    const result = await api<Contact[]>(`/roleplay/qq/accounts/${target}/contacts`)
    if (version === contactVersion && accountId.value === target) contacts.value = result
  } catch (cause) { if (version === contactVersion) error.value = errorText(cause) }
}
async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const result = await Promise.all([api<QQAccountsResponse>('/roleplay/qq/accounts'), api<Character[]>('/roleplay/characters'), api<RoleInstance[]>('/roleplay/instances')])
    if (version !== loadVersion) return
    accounts.value = result[0].items; characters.value = result[1]; instances.value = result[2]; error.value = ''
    if (!accounts.value.some(item => item.id === accountId.value)) accountId.value = accounts.value[0]?.id ?? ''
    else await loadContacts()
    if (selectedInstance.value) selectedInstance.value = instances.value.find(item => item.id === selectedInstance.value?.id) ?? selectedInstance.value
    openRequested()
  } catch (cause) { if (version === loadVersion) error.value = errorText(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
function edit(contact?: Contact) {
  editingPeer.value = !!contact
  Object.assign(form, { account_id: accountId.value, peer_id: contact?.peer_id ?? '', character_id: contact?.character_id ?? characters.value[0]?.id ?? '', display_name: contact?.display_name ?? '', enabled: contact?.instance_id ? !!contact.enabled : true, revision: contact?.revision ?? 0 })
  bindingError.value = ''; bindingVisible.value = true
}
async function saveBinding() {
  if (saving.value) return
  saving.value = true; bindingError.value = ''
  try {
    const { account_id, peer_id, ...body } = form
    await api(`/roleplay/qq/accounts/${account_id}/contacts/${encodeURIComponent(peer_id)}`, { method: 'PUT', body: JSON.stringify(body) })
    bindingVisible.value = false; ElMessage.success('联系人绑定已保存'); await load()
  } catch (cause) { bindingError.value = errorText(cause) }
  finally { saving.value = false }
}
function openContact(contact: Contact) {
  const instance = instances.value.find(item => item.id === contact.instance_id)
  if (instance) openMessages(instance)
}
function openMessages(instance: RoleInstance) {
  selectedInstance.value = instance; selectedLifeLine.value = instance.id; detailTab.value = 'conversation'; messages.value = []; nextBefore.value = null
  messageVersion++; messageLoading.value = false; messageError.value = ''; messageVisible.value = true; messageUpdates.value = false
}
async function loadMessages(more = false) {
  const target = selectedInstance.value?.id
  if (!target || (more && !nextBefore.value)) return
  const version = ++messageVersion
  messageLoading.value = true
  const before = more ? `?before=${nextBefore.value}` : ''
  try {
    const result = await api<InputMessagesPage>(`/roleplay/instances/${target}/messages${before}`)
    if (version !== messageVersion || !messageVisible.value || selectedInstance.value?.id !== target) return
    messages.value = more ? [...messages.value, ...result.items] : result.items
    nextBefore.value = result.next_before; messageError.value = ''; if (!more) messageUpdates.value = false
  } catch (cause) { if (version === messageVersion) messageError.value = errorText(cause) }
  finally { if (version === messageVersion) messageLoading.value = false }
}
async function retry(imageId: string) {
  if (!selectedInstance.value || busyImage.value) return
  busyImage.value = imageId
  try {
    await api(`/roleplay/instances/${selectedInstance.value.id}/images/${imageId}/retry`, { method: 'POST' })
    await loadMessages()
  } catch (cause) { messageError.value = errorText(cause) }
  finally { busyImage.value = '' }
}
async function onConversationDeleted() {
  conversationKey.value++
  messageVersion++; messages.value = []; nextBefore.value = null; messageLoading.value = false; messageUpdates.value = false
  await load()
}
watch(selectedLifeLine, openRequested)
watch(detailTab, value => { if (value === 'messages' && messageVisible.value && !messages.value.length) void loadMessages() })
watch(accountId, () => { contacts.value = []; void loadContacts() })
watch(() => props.refreshKey, () => { if (messageVisible.value) messageUpdates.value = true; void load() })
onMounted(load)
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><el-select v-model="accountId" placeholder="选择接入账号" class="account-select"><el-option v-for="item in accounts" :key="item.id" :label="`${item.name} · ${item.bot_id}`" :value="item.id" /></el-select><el-button type="primary" :disabled="!accountId || !characters.length" @click="edit()">绑定联系人</el-button></div>
  <section v-if="!accounts.length" class="panel" v-loading="loading"><el-empty description="请先添加 QQ 接入账号"><a href="#qq"><el-button>配置 QQ</el-button></a></el-empty></section>
  <template v-else>
    <el-alert v-if="!characters.length" title="请先创建角色，再绑定联系人。" type="info" :closable="false" class="form-alert" />
    <div class="section-toolbar"><el-input v-model="search" class="account-select" placeholder="搜索联系人、QQ 或角色" aria-label="搜索联系人" clearable /><el-button v-if="search" link @click="search = ''">清除筛选</el-button></div>
    <el-tabs v-model="tab" class="management-tabs">
      <el-tab-pane label="联系人" name="contacts">
        <section class="panel table-scroll" v-loading="loading"><el-table :data="visibleContacts" empty-text="暂无联系人，可手动绑定 QQ 号">
          <el-table-column label="联系人" min-width="150"><template #default="{ row }"><strong>{{ row.display_name || row.nickname || row.peer_id }}</strong><div class="contact-number">{{ row.peer_id }}</div></template></el-table-column>
          <el-table-column label="角色" min-width="130"><template #default="{ row }">{{ row.character_name || '未绑定' }}</template></el-table-column>
          <el-table-column label="接收" width="90"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" effect="plain">{{ row.enabled ? '已开启' : '未开启' }}</el-tag></template></el-table-column>
          <el-table-column label="操作" min-width="145"><template #default="{ row }"><el-button link :disabled="!characters.length" @click="edit(row)">{{ row.instance_id ? '编辑绑定' : '绑定角色' }}</el-button><el-button v-if="row.instance_id" link @click="openContact(row)">管理</el-button></template></el-table-column>
        </el-table></section>
      </el-tab-pane>
      <el-tab-pane label="生活线与记录" name="history">
        <section class="panel table-scroll"><el-table :data="history" empty-text="绑定联系人后建立独立生活线">
          <el-table-column label="联系人" min-width="135"><template #default="{ row }">{{ row.display_name || row.nickname || row.peer_id }}</template></el-table-column>
          <el-table-column label="角色设定" min-width="150"><template #default="{ row }">{{ row.character_name }}</template></el-table-column>
          <el-table-column label="接收" width="90"><template #default="{ row }">{{ row.receiving ? '当前使用' : '已保留' }}</template></el-table-column>
          <el-table-column label="操作" min-width="100"><template #default="{ row }"><el-button link @click="openMessages(row)">管理</el-button></template></el-table-column>
        </el-table></section>
      </el-tab-pane>
    </el-tabs>
  </template>
  <EditDialog ref="bindingDialog" :draft="form" :busy="saving" v-model="bindingVisible" title="联系人绑定" width="550px" class="model-dialog" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <el-alert v-if="bindingError" :title="bindingError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="saving" @submit.prevent="saveBinding">
      <el-form-item label="联系人 QQ"><el-input v-model="form.peer_id" :disabled="editingPeer" inputmode="numeric" maxlength="20" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="form.display_name" maxlength="80" /></el-form-item>
      <el-form-item label="角色"><el-select v-model="form.character_id"><el-option v-for="item in characters" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
      <div class="form-actions editor-actions"><el-switch v-model="form.enabled" active-text="接收消息" /><el-button @click="bindingDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!form.character_id || !form.peer_id">保存</el-button></div>
    </el-form>
  </EditDialog>
  <EditorWorkspace ref="detailEditor" v-model="messageVisible" :title="`${selectedInstance?.display_name || selectedInstance?.nickname || selectedInstance?.peer_id || ''} · ${selectedInstance?.character_name || ''}`" :scope="`${selectedInstance?.account_name || ''} · QQ ${selectedInstance?.peer_id || ''}`" :version="selectedInstance?.character_revision" :draft="null" guard-children @closed="messageVersion++">
    <div class="section-toolbar life-line-heading"><div><el-tag effect="plain" :type="selectedInstance?.receiving ? 'success' : 'info'">{{ selectedInstance?.receiving ? '当前接收' : '保留的生活线' }}</el-tag><a v-if="selectedInstance" :href="`#runtime?instance=${encodeURIComponent(selectedInstance.id)}`" class="action-link">运行台账 →</a></div><ClearContextButton v-if="selectedInstance" :instance-id="selectedInstance.id" :timezone="timezone" @cleared="onConversationDeleted" /></div>
    <div class="detail-section-navigation"><el-tabs v-model="detailTab" :before-leave="() => mayLeave()" class="management-tabs"><el-tab-pane label="对话记录" name="conversation" /><el-tab-pane label="输入与附件" name="messages" /><el-tab-pane label="私有记忆" name="memories" /><el-tab-pane label="上下文" name="context" /><el-tab-pane label="生活与投递" name="life" /><el-tab-pane label="日程与资料" name="materials" /><el-tab-pane v-if="advancedTabs[detailTab]" :label="advancedTabs[detailTab]" :name="detailTab" /></el-tabs><el-dropdown trigger="click" @command="openAdvanced"><el-button>更多 ⌄</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-for="(label, value) in advancedTabs" :key="value" :command="value">{{ label }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div>
    <ConversationHistory v-if="detailTab === 'conversation' && selectedInstance" :key="`${selectedInstance.id}:${conversationKey}`" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" @deleted="onConversationDeleted" />
    <div v-show="detailTab === 'messages'">

    <div class="section-toolbar"><el-tag effect="plain" type="info">QQ 输入记录</el-tag><el-button :type="messageUpdates ? 'primary' : 'default'" :loading="messageLoading" @click="loadMessages()">{{ messageUpdates ? '有变化 · 刷新记录' : '刷新记录' }}</el-button></div>
    <el-alert v-if="messageError" :title="messageError" type="error" :closable="false" class="form-alert" />
    <el-empty v-if="!messages.length && !messageLoading" description="尚未收到消息" />
    <article v-for="message in messages" :key="message.id" class="input-record">
      <div class="input-record-heading"><time>{{ formatTime(message.event_at, timezone) }}</time><span>消息 {{ message.message_id }}</span></div>
      <div v-for="(part, index) in message.parts" :key="index" class="input-part">
        <p v-if="part.type === 'text'" class="message-text">{{ part.text }}</p>
        <template v-else-if="part.type === 'image'"><el-image v-if="part.status === 'ready' && part.url" :src="part.url" :preview-src-list="[part.url]" preview-teleported fit="contain" class="message-image"><template #error>图片暂时无法显示</template></el-image><div v-else-if="part.status === 'failed'" class="image-failure"><span>{{ part.error || '图片接收失败' }}</span><el-button link :loading="busyImage === part.image_id" @click="retry(part.image_id)">重新接收</el-button></div><el-tag v-else effect="plain" type="info">图片接收中</el-tag></template>
        <el-tag v-else-if="part.type === 'reply'" effect="plain" type="info">引用消息 {{ part.message_id }}</el-tag>
        <el-tag v-else effect="plain" type="warning">暂不支持：{{ part.kind }}</el-tag>
      </div>
    </article>
    <div v-if="nextBefore" class="load-more"><el-button :loading="messageLoading" @click="loadMessages(true)">更早的记录</el-button></div>
    </div>
    <MemoriesPage v-if="detailTab === 'memories'" :refresh-key="refreshKey" :timezone="timezone" embedded />
    <ContextPage v-if="detailTab === 'context' && selectedInstance" :key="`${selectedInstance.id}:${conversationKey}`" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" embedded />
    <LifeMaterials v-if="detailTab === 'materials' && selectedInstance" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" />
    <EvidencePanel v-if="detailTab === 'evidence' && selectedInstance" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" />
    <CharacterAdjustments v-if="detailTab === 'adjustments' && selectedInstance" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" />
    <McpPermissions v-if="detailTab === 'mcp' && selectedInstance" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" />
    <LifePage v-if="detailTab === 'life' && selectedInstance" :instance-id="selectedInstance.id" :refresh-key="refreshKey" :timezone="timezone" />
    <RuntimePage v-if="detailTab === 'runtime' && selectedInstance" :refresh-key="refreshKey" :timezone="timezone" :instance-id="selectedInstance.id" />
  </EditorWorkspace>
</template>
