<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { ServerStatus, SystemInfo } from '../api'
import type { RuntimeOverview } from '../runtime-types'
import type { LifeOverview, LifeTimeline } from '../life-types'
import type { ModelCatalog } from '../model-types'
import type { Character } from '../roleplay-types'
const props = defineProps<{ refreshKey: number; timezone: string }>()
const status = ref<ServerStatus | null>(null), info = ref<SystemInfo | null>(null), runtime = ref<RuntimeOverview | null>(null)
const life = ref<LifeOverview | null>(null), catalog = ref<ModelCatalog | null>(null), characters = ref<Character[] | null>(null), timeline = ref<LifeTimeline | null>(null)
const loading = ref(false), failures = ref<string[]>([]), updates = ref(false), loadedAt = ref<number | null>(null)
let version = 0
const fresh = (name: string) => !failures.value.some(value => value.startsWith(name + '：'))
const tasks = computed(() => {
  const items: { title: string; href: string; action: string }[] = []
  if (status.value && fresh('服务状态')) {
    if (!status.value.models.connections) items.push({ title: '尚未添加模型连接', href: '#models', action: '添加连接' })
    if (!status.value.qq.accounts) items.push({ title: '尚未添加 QQ 接入', href: '#qq', action: '配置 QQ' })
    else if (!status.value.qq.connected) items.push({ title: '当前没有在线的 QQ 连接', href: '#qq', action: '查看接入' })
  }
  if (catalog.value?.connections.length && !catalog.value.assignments.narrator && fresh('模型配置')) items.push({ title: '主叙事模型尚未分配', href: '#models', action: '分配用途' })
  if (characters.value && !characters.value.length && fresh('角色资料')) items.push({ title: '尚未建立角色模板', href: '#characters', action: '创建或导入' })
  if (runtime.value && !runtime.value.settings.enabled && fresh('运行状态')) items.push({ title: '自动回复未开启', href: '#runtime', action: '运行设置' })
  if (life.value && fresh('主动投递')) {
    if (!life.value.settings.enabled) items.push({ title: '生活运行未开启', href: '#life', action: '生活设置' })
    else if (runtime.value && fresh('运行状态') && runtime.value.instances.some(line => line.receiving && !line.paused && !life.value!.policies.some(policy => policy.instance_id === line.id && policy.enabled)))
      items.push({ title: '有生活线未开启主动投递', href: '#life', action: '查看生活线' })
  }
  if (runtime.value?.summary.last_error && fresh('运行状态')) items.push({ title: runtime.value.summary.last_error, href: '#runtime', action: '查看异常' })
  return items
})
async function load() {
  const mine = ++version; loading.value = true
  const values = await Promise.allSettled([api<ServerStatus>('/system/status'), api<SystemInfo>('/system/info'), api<RuntimeOverview>('/roleplay/runtime'), api<LifeOverview>('/roleplay/life'), api<ModelCatalog>('/models/catalog'), api<Character[]>('/roleplay/characters'), api<LifeTimeline>('/roleplay/life/timeline')])
  if (mine !== version) return
  const names = ['服务状态', '服务信息', '运行状态', '主动投递', '模型配置', '角色资料', '最近生活']
  failures.value = values.flatMap((value, index) => value.status === 'rejected' ? [`${names[index]}：${errorText(value.reason)}`] : [])
  if (values[0].status === 'fulfilled') status.value = values[0].value
  if (values[1].status === 'fulfilled') info.value = values[1].value
  if (values[2].status === 'fulfilled') runtime.value = values[2].value
  if (values[3].status === 'fulfilled') life.value = values[3].value
  if (values[4].status === 'fulfilled') catalog.value = values[4].value
  if (values[5].status === 'fulfilled') characters.value = values[5].value
  if (values[6].status === 'fulfilled') timeline.value = values[6].value
  loadedAt.value = Date.now() / 1000; loading.value = false; updates.value = false
}
function eventText(raw: string) {
  try { const value = JSON.parse(raw); return String(value.life_fragment || value.reason || (value.status === 'succeeded' ? value.text : '') || value.error || '状态记录已更新') }
  catch { return '查看生活记录详情' }
}
watch(() => props.refreshKey, () => { updates.value = true })
onMounted(load)
onScopeDispose(() => { version++ })
</script>
<template>
  <div class="page-heading overview-heading"><h1>运行概览</h1><div class="overview-heading-actions"><span class="muted">{{ loadedAt ? '读取于 ' + formatTime(loadedAt, timezone) : '正在获取状态' }}</span><el-button :type="updates ? 'primary' : 'default'" :loading="loading" @click="load">{{ updates ? '有更新 · 刷新概览' : '刷新概览' }}</el-button></div></div>
  <el-alert v-if="failures.length" title="部分信息未获取，原有数据仅供参考。" type="warning" :closable="false" class="form-alert"><details><summary>查看原因</summary><p v-for="failure in failures" :key="failure">{{ failure }}</p></details></el-alert>
  <el-skeleton v-if="loading && !loadedAt" :rows="5" animated />
  <template v-if="loadedAt">
    <section class="overview-status" aria-label="运行状态">
      <span :class="['status-pill', { online: status && fresh('服务状态') }]"><i aria-hidden="true"></i>{{ status && fresh('服务状态') ? '服务有响应' : '服务状态待更新' }}</span>
      <a href="#qq" :class="['status-pill', { online: status && fresh('服务状态') && status.qq.connected > 0 && status.qq.connected === status.qq.accounts, warning: status && fresh('服务状态') && status.qq.connected < status.qq.accounts }]"><i aria-hidden="true"></i>QQ {{ status && fresh('服务状态') ? status.qq.connected + ' / ' + status.qq.accounts : '未知' }}</a>
      <a href="#runtime" :class="['status-pill', { online: runtime && fresh('运行状态') && runtime.settings.enabled }]"><i aria-hidden="true"></i>自动回复：{{ runtime && fresh('运行状态') ? runtime.settings.enabled ? '开启' : '关闭' : '未知' }}</a>
      <a href="#life" :class="['status-pill', { online: life && fresh('主动投递') && life.settings.enabled }]"><i aria-hidden="true"></i>主动投递：{{ life && fresh('主动投递') ? life.settings.enabled ? '开启' : '关闭' : '未知' }}</a>
    </section>
    <section class="panel section-gap"><div class="panel-heading"><h2>需要处理</h2><span class="muted">{{ tasks.length }} 项</span></div><article v-for="task in tasks" :key="task.title" class="attention-row"><span>{{ task.title }}</span><a :href="task.href" class="action-link">{{ task.action }} →</a></article><p v-if="!tasks.length" class="muted">{{ failures.length ? '先恢复缺失的信息，再查看待处理事项。' : '已获取的信息中暂无待处理事项。' }}</p></section>
    <dl class="overview-numbers"><div class="metric-card"><dt>模型连接</dt><dd>{{ status && fresh('服务状态') ? status.models.connections : '未知' }}</dd></div><div class="metric-card"><dt>独立生活线</dt><dd>{{ status && fresh('服务状态') ? status.qq.instances : '未知' }}</dd></div><div class="metric-card"><dt>待处理输入</dt><dd>{{ runtime && fresh('运行状态') ? runtime.instances.reduce((sum, item) => sum + item.pending_inputs, 0) : '未知' }}</dd></div><div class="metric-card"><dt>生成／投递中</dt><dd>{{ runtime && fresh('运行状态') ? runtime.summary.generating + ' / ' + runtime.summary.sending : '未知' }}</dd></div></dl>
    <div class="overview-workspace"><section class="panel"><div class="panel-heading"><h2>生活线</h2><a href="#contacts" class="action-link">全部联系人 →</a></div><el-empty v-if="!runtime?.instances.length" :description="runtime && fresh('运行状态') ? '绑定联系人后建立独立生活线' : '生活线信息未获取'" :image-size="50" /><a v-for="item in runtime?.instances.slice(0, 6) ?? []" :key="item.id" :href="`#contacts?instance=${encodeURIComponent(item.id)}`" class="life-line-link"><div><strong>{{ item.display_name || item.nickname || item.peer_id }} · {{ item.character_name }}</strong><span>{{ item.account_name }}</span></div><span>{{ item.paused ? '已暂停' : item.receiving ? '接收中' : '已保留' }} →</span></a></section><section class="panel"><div class="panel-heading"><h2>最近生活</h2><a href="#life" class="action-link">时间线 →</a></div><el-empty v-if="!timeline?.items.length" :description="timeline && fresh('最近生活') ? '尚无生活记录' : '最近生活未获取'" :image-size="50" /><a v-for="item in timeline?.items.slice(0, 5) ?? []" :key="item.id" :href="`#life?instance=${encodeURIComponent(item.instance_id)}`" class="overview-event"><time>{{ formatTime(item.created_at, timezone) }}</time><p>{{ eventText(item.data_json) }}</p></a></section></div>
    <section v-if="status || info" class="panel section-gap"><el-collapse><el-collapse-item title="服务与运行环境" name="service"><dl class="detail-list overview-details"><div><dt>服务版本</dt><dd>{{ status?.version ?? '未知' }}</dd></div><div><dt>运行环境</dt><dd>{{ info ? info.python + ' / ' + info.platform : '未知' }}</dd></div><div><dt>数据库</dt><dd>{{ status ? 'SQLite / ' + status.database.journal_mode + ' / v' + status.database.schema_version : '未知' }}</dd></div><div><dt>管理地址</dt><dd>{{ info?.public_origin ?? '未知' }}</dd></div><div><dt>数据位置</dt><dd>{{ info?.data_dir ?? '未知' }}</dd></div><div><dt>凭据保护</dt><dd>{{ status ? status.credentials.encrypted ? '已加密' : '未加密' : '未知' }}</dd></div></dl></el-collapse-item></el-collapse></section>
  </template>
</template>
