<script setup lang="ts">
import EditDialog from '../components/EditDialog.vue'
import NavIcon from '../components/NavIcon.vue'
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { QQAccount, QQAccountsResponse } from '../roleplay-types'

const qqDialog = ref<InstanceType<typeof EditDialog> | null>(null)
const props = defineProps<{ refreshKey: number; timezone: string }>()
const items = ref<QQAccount[]>([]), error = ref(''), loading = ref(false), visible = ref(false), saving = ref(false)
const editorError = ref(''), imageError = ref<string | null>(null), editingId = ref<string | null>(null)
const disconnecting = ref<string | null>(null)
const form = reactive({ name: '', bot_id: '', enabled: true, token: '', revision: 0 })
let loadVersion = 0
async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const result = await api<QQAccountsResponse>('/roleplay/qq/accounts')
    if (version === loadVersion) { items.value = result.items; imageError.value = result.images.last_error; error.value = '' }
  } catch (cause) { if (version === loadVersion) error.value = errorText(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
function edit(item?: QQAccount) {
  editingId.value = item?.id ?? null
  Object.assign(form, { name: item?.name ?? '', bot_id: item?.bot_id ?? '', enabled: item?.enabled ?? true, token: '', revision: item?.revision ?? 0 })
  editorError.value = ''; visible.value = true
}
function generateToken() {
  form.token = Array.from(crypto.getRandomValues(new Uint8Array(24)), value => value.toString(16).padStart(2, '0')).join('')
}
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); ElMessage.success('已复制') }
  catch { ElMessage.warning('无法自动复制，请选中后手动复制。') }
}
async function save() {
  if (saving.value) return
  saving.value = true; editorError.value = ''
  try {
    await api(`/roleplay/qq/accounts${editingId.value ? '/' + editingId.value : ''}`, {
      method: editingId.value ? 'PUT' : 'POST', body: JSON.stringify({ ...form, token: form.token || null }),
    })
    form.token = ''; visible.value = false; ElMessage.success('接入配置已保存'); await load()
  } catch (cause) { editorError.value = errorText(cause) }
  finally { saving.value = false }
}
async function disconnect(item: QQAccount) {
  if (disconnecting.value) return
  disconnecting.value = item.id
  try { await api(`/roleplay/qq/accounts/${item.id}/disconnect`, { method: 'POST' }); await load() }
  catch (cause) { error.value = errorText(cause) }
  finally { disconnecting.value = null }
}
watch(() => props.refreshKey, load)
onMounted(load)
</script>

<template>
  <el-alert v-if="error || imageError" :title="error || imageError || ''" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span>{{ items.length }} 个接入账号</span><el-button type="primary" @click="edit()">添加账号</el-button></div>
  <section v-if="!items.length" class="panel" v-loading="loading"><el-empty description="尚未配置 QQ" /></section>
  <div class="connection-list">
    <section v-for="item in items" :key="item.id" class="panel qq-account-card">
      <div class="connection-heading"><div class="qq-account-identity"><span :class="['qq-connection-symbol', { online: item.enabled && item.connection.connected }]" aria-hidden="true"><NavIcon name="integrations" /></span><div><h2>{{ item.name }}</h2><div class="connection-meta"><span>QQ {{ item.bot_id }}</span><span v-if="item.connection.last_event_at">最近上报 {{ formatTime(item.connection.last_event_at, timezone) }}</span></div></div></div><div class="connection-actions"><span :class="['qq-connection-state', { online: item.enabled && item.connection.connected, disabled: !item.enabled }]" role="status"><span aria-hidden="true"></span>{{ !item.enabled ? '已停用' : item.connection.connected ? '网关已连接' : '网关未连接' }}</span><el-button @click="edit(item)">编辑</el-button><el-button v-if="item.connection.connected || item.connection.handshake_error_code === 'already_connected'" :loading="disconnecting === item.id" @click="disconnect(item)">{{ item.connection.connected ? '断开' : '清理旧连接' }}</el-button></div></div>
      <div class="qq-url"><el-input :model-value="item.websocket_url" readonly aria-label="反向 WebSocket 地址" /><el-button @click="copy(item.websocket_url)">复制地址</el-button></div>
      <el-alert v-if="item.connection.handshake_error" :title="item.connection.handshake_error" :description="`${item.connection.handshake_error_code ?? ''}${item.connection.handshake_failed_at ? ' · ' + formatTime(item.connection.handshake_failed_at, timezone) : ''}`" type="warning" :closable="false" class="section-gap" />
      <el-alert v-if="item.connection.last_error" :title="item.connection.last_error" type="warning" :closable="false" class="section-gap" />
    </section>
  </div>
  <section class="panel section-gap"><el-collapse class="quiet-collapse"><el-collapse-item title="NapCat 连接设置" name="napcat"><dl class="detail-list"><div><dt>网络类型</dt><dd>WebSocket 客户端／反向连接</dd></div><div><dt>地址</dt><dd>上方对应账号的连接地址</dd></div><div><dt>Token</dt><dd>添加账号时设置的连接令牌</dd></div><div><dt>消息格式</dt><dd>array（数组）</dd></div><div><dt>连接类型</dt><dd>Universal</dd></div></dl><a href="#runtime">运行与投递</a></el-collapse-item></el-collapse></section>
  <EditDialog ref="qqDialog" :draft="form" :busy="saving" v-model="visible" :title="editingId ? '编辑 QQ 接入' : '添加 QQ 接入'" width="580px" class="model-dialog" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="form.token = ''">
    <el-alert v-if="editorError" :title="editorError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="saving" @submit.prevent="save">
      <el-form-item label="账号名称"><el-input v-model="form.name" maxlength="80" /></el-form-item>
      <el-form-item label="机器人 QQ"><el-input v-model="form.bot_id" :disabled="!!editingId" inputmode="numeric" maxlength="20" /></el-form-item>
      <el-form-item :label="editingId ? '新连接令牌（留空保留）' : '连接令牌'"><el-input v-model="form.token" type="password" show-password maxlength="256" autocomplete="new-password" /></el-form-item>
      <div class="form-actions"><el-button @click="generateToken">生成令牌</el-button><el-button :disabled="!form.token" @click="copy(form.token)">复制令牌</el-button></div>
      <p class="credential-notice">保存前复制到 NapCat；保存后不再显示令牌。</p>
      <div class="form-actions editor-actions"><el-switch v-model="form.enabled" active-text="启用" /><el-button @click="qqDialog?.close()">取消</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!form.name.trim() || !form.bot_id || (!editingId && form.token.length < 24)">保存</el-button></div>
    </el-form>
  </EditDialog>
</template>
