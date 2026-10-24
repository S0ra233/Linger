<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { AuditEvent, AuditPage } from '../api'

const props = defineProps<{ refreshKey: number; timezone: string }>()
const items = ref<AuditEvent[]>([]), next = ref<number | null>(null)
const loading = ref(false), error = ref('')
const labels: Record<string, string> = {
  'usage.prices_saved': '更新自设价格',
  'backup.succeeded': '完成数据备份', 'backup.failed': '数据备份失败',
  'backup.interrupted': '备份已中止', 'backup.restored_offline': '停服恢复备份',
  'system.initialized': '工作空间初始化', 'auth.login': '管理员登录', 'auth.login_failed': '登录未成功',
  'auth.logout': '管理员退出', 'auth.sessions_revoked': '撤销其他登录', 'auth.password_changed': '修改管理员密码',
  'auth.password_reset_offline': '本机重置管理员密码', 'preferences.updated': '更新基础设置',
  'models.saved': '保存模型连接', 'models.assignments_saved': '保存模型用途',
  'models.archived': '归档模型连接', 'models.restored': '恢复模型连接', 'models.check_started': '发起连接检查',
  'character.saved': '保存角色设定', 'qq.account_saved': '保存 QQ 接入', 'contact.bound': '更新联系人绑定',
  'instance.character_synced': '更新生活线设定', 'image.retry': '重新接收图片',
  'rp.settings_saved': '更新自动运行设置', 'rp.pause': '暂停生活线', 'rp.resume': '恢复生活线', 'rp.retry': '重新评估输入',
  'rp.delivery_resolved': '人工确认投递结果', 'rp.delivery_cancelled': '取消投递计划',
  'worldbook.saved': '保存世界书', 'context.settings_saved': '更新上下文设置', 'context.summary_cleared': '停用历史摘要',
  'preset.saved': '保存模型预设',
  'memory.saved': '更新私有记忆', 'library.saved': '更新资料库', 'document.saved': '更新知识文档',
}
async function load(append = false) {
  if (loading.value) return
  loading.value = true
  try {
    const result = await api<AuditPage>(`/audit${append && next.value ? `?before=${next.value}` : ''}`)
    items.value = append ? [...items.value, ...result.items] : result.items; next.value = result.next_before; error.value = ''
  } catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}
function detail(item: AuditEvent) {
  if (item.action === 'preferences.updated') return `配置修订 ${item.details.revision}`
  if (item.action === 'auth.sessions_revoked') return `已撤销 ${item.details.count} 个会话`
  if (item.details.peer) return `来源 ${item.details.peer}`
  if (item.details.username) return `管理员 ${item.details.username}`
  return '操作已记录'
}
watch(() => props.refreshKey, () => load())
onMounted(() => load())
</script>

<template>
  <section class="panel"><div class="panel-heading"><h2>管理操作</h2><el-button :loading="loading" @click="load()">刷新</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" /><el-skeleton v-if="loading && !items.length" :rows="5" animated /><el-empty v-else-if="!items.length" description="暂无管理操作记录" :image-size="80" /><div v-else class="audit-list"><div v-for="item in items" :key="item.id" class="audit-row"><span class="audit-marker"></span><div><h3>{{ labels[item.action] ?? item.action }}</h3><p>{{ detail(item) }} <span class="record-id">#{{ item.id }}</span></p></div><time>{{ formatTime(item.created_at, timezone) }}</time></div></div><div v-if="next" class="load-more"><el-button :loading="loading" @click="load(true)">载入更早记录</el-button></div></section>
</template>
