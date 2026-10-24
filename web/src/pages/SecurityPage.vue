<script setup lang="ts">
import { onMounted, onScopeDispose, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { BrowserSession } from '../api'
import { protectPage } from '../ui-state'

const props = defineProps<{ refreshKey: number; timezone: string }>()
const emit = defineEmits<{ passwordChanged: [] }>()
const sessions = ref<BrowserSession[]>([])
const error = ref(''), passwordError = ref('')
const loading = ref(false), revoking = ref(false), saving = ref(false)
const form = reactive({ current_password: '', new_password: '', confirm_password: '' })
// 只缩短展示标签；保留原始 User-Agent，未知来源不推断成已知设备。
function sessionDevice(agent: string | null) {
  if (!agent) return '未提供浏览器信息'
  const platform = /iPhone|iPad|iPod|Macintosh.*Mobile/i.test(agent) ? 'iOS' : /Android/i.test(agent) ? 'Android' : /Windows/i.test(agent) ? 'Windows' : /CrOS/i.test(agent) ? 'ChromeOS' : /Macintosh|Mac OS X/i.test(agent) ? 'macOS' : /Linux/i.test(agent) ? 'Linux' : '未知设备'
  const browser = /Edg(?:e|A|iOS)?\//i.test(agent) ? 'Edge' : /OPR\/|Opera/i.test(agent) ? 'Opera' : /Firefox\/|FxiOS\//i.test(agent) ? 'Firefox' : /Chrome\/|CriOS\//i.test(agent) ? 'Chrome' : /Safari\//i.test(agent) ? 'Safari' : '未知浏览器'
  return `${platform} · ${browser}`
}
protectPage(() => saving.value || revoking.value)
let loadVersion = 0
async function load() {
  const mine = ++loadVersion
  loading.value = true
  try { const value = await api<BrowserSession[]>('/auth/sessions'); if (mine === loadVersion) { sessions.value = value; error.value = '' } }
  catch (cause) { if (mine === loadVersion) error.value = errorText(cause) }
  finally { if (mine === loadVersion) loading.value = false }
}
async function revokeOthers() {
  if (revoking.value) return
  try { await ElMessageBox.confirm('其他浏览器将需要重新登录，当前页面继续保留。', '撤销其他登录', { confirmButtonText: '撤销', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  revoking.value = true; loadVersion++; loading.value = false
  try {
    const result = await api<{ revoked: number }>('/auth/sessions/others', { method: 'DELETE' })
    ElMessage.success(`已撤销 ${result.revoked} 个其他会话`); await load()
  } catch (cause) { error.value = errorText(cause) }
  finally { revoking.value = false }
}
async function changePassword() {
  if (saving.value) return
  passwordError.value = ''
  if (!form.current_password || form.new_password.length < 12) { passwordError.value = '请填写当前密码，新密码至少需要 12 个字符。'; return }
  if (form.new_password !== form.confirm_password) { passwordError.value = '两次新密码不一致。'; return }
  saving.value = true
  try {
    await api<void>('/auth/password', { method: 'POST', body: JSON.stringify({ current_password: form.current_password, new_password: form.new_password }) })
    Object.assign(form, { current_password: '', new_password: '', confirm_password: '' })
    ElMessage.success('密码已更新，请使用新密码重新登录'); emit('passwordChanged')
  } catch (cause) { passwordError.value = errorText(cause) }
  finally { saving.value = false }
}
watch(() => props.refreshKey, load)
onMounted(load)
onScopeDispose(() => { loadVersion++ })
</script>

<template>
  <section class="panel"><div class="panel-heading"><h2>浏览器登录</h2><el-button :loading="revoking" :disabled="!sessions.some(item => !item.current)" @click="revokeOthers">撤销其他登录</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <div class="table-scroll"><el-table :data="sessions" v-loading="loading" empty-text="没有可用的登录记录"><el-table-column label="来源" min-width="170"><template #default="scope"><strong class="session-source">{{ scope.row.peer }}</strong><el-tag v-if="scope.row.current" size="small" effect="plain" class="current-session">当前</el-tag><el-tooltip :content="scope.row.user_agent || '未提供浏览器信息'" :trigger="['hover', 'focus']" placement="top"><small class="session-agent" tabindex="0">{{ sessionDevice(scope.row.user_agent) }}</small></el-tooltip></template></el-table-column><el-table-column label="登录信息" min-width="250"><template #default="scope"><dl class="session-times"><div><dt>登录</dt><dd>{{ formatTime(scope.row.created_at, timezone) }}</dd></div><div><dt>最近活动</dt><dd>{{ formatTime(scope.row.last_seen_at, timezone) }}</dd></div><div><dt>最晚到期</dt><dd>{{ formatTime(scope.row.expires_at, timezone) }}</dd></div></dl></template></el-table-column></el-table></div>
  </section>
  <section class="panel settings-main compact-form section-gap"><div class="panel-heading"><h2>修改密码</h2></div>
    <el-alert v-if="passwordError" :title="passwordError" type="error" :closable="false" class="form-alert" />
    <el-form :model="form" class="password-form" label-position="top" @submit.prevent="changePassword"><el-form-item label="当前密码" class="current-password-field"><el-input v-model="form.current_password" type="password" show-password autocomplete="current-password" maxlength="128" :disabled="saving" /></el-form-item><div class="form-two-columns"><el-form-item label="新密码（12—128 字符）"><el-input v-model="form.new_password" type="password" show-password autocomplete="new-password" maxlength="128" :disabled="saving" /></el-form-item><el-form-item label="确认新密码"><el-input v-model="form.confirm_password" type="password" show-password autocomplete="new-password" maxlength="128" :disabled="saving" /></el-form-item></div><el-button type="primary" native-type="submit" :loading="saving">更新密码并退出全部登录</el-button></el-form>
  </section>
</template>
