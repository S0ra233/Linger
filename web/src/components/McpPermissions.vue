<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { McpCall, McpCatalog, McpPermissions, McpGrant } from '../mcp-types'
import { mcpStatus } from '../mcp-types'
import { protectPage } from '../ui-state'
import DataDetails from './DataDetails.vue'

const props = defineProps<{ instanceId: string; refreshKey: number; timezone: string }>()
const catalog = ref<McpCatalog | null>(null), permission = ref<McpPermissions | null>(null), calls = ref<McpCall[]>([])
const chosen = ref<string[]>([]), baseline = ref('[]'), error = ref(''), loading = ref(false), saving = ref(false), updates = ref(false)
const dirty = computed(() => JSON.stringify(chosen.value) !== baseline.value)
const detail = ref<McpCall | null>(null), visible = ref(false)
const key = (tool: { server_id: string; name: string }) => JSON.stringify([tool.server_id, tool.name])
const available = computed(() => (catalog.value?.servers ?? []).filter(server => server.enabled).map(server => ({ ...server, choices: server.tools.filter(tool => tool.eligible).map(tool => ({ value: key({ server_id: server.id, name: tool.name }), label: tool.name, grant: { server_id: server.id, name: tool.name, catalog_revision: server.catalog_revision } })) })))
const missing = computed(() => chosen.value.filter(value => !available.value.some(server => server.choices.some(choice => choice.value === value))))
let version = 0
protectPage(() => saving.value)
async function load() {
  if (saving.value) return
  const mine = ++version, id = props.instanceId; loading.value = true
  try {
    const [value, grants, history] = await Promise.all([api<McpCatalog>('/mcp'), api<McpPermissions>(`/mcp/instances/${id}/permissions`), api<McpCall[]>(`/mcp/calls?instance_id=${encodeURIComponent(id)}`)])
    if (mine !== version) return
    catalog.value = value; permission.value = grants; calls.value = history
    chosen.value = grants.tools.map(key); baseline.value = JSON.stringify(chosen.value); updates.value = false; error.value = ''
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function save() {
  if (saving.value || !permission.value) return
  const tools: McpGrant[] = []
  for (const value of chosen.value) {
    const item = available.value.flatMap(server => server.choices).find(choice => choice.value === value)
    if (!item) { error.value = '有工具已失效，请移除后保存或重新载入。'; return }
    tools.push(item.grant)
  }
  saving.value = true
  try {
    const result = await api<McpPermissions>(`/mcp/instances/${props.instanceId}/permissions`, { method: 'PUT', body: JSON.stringify({ revision: permission.value.revision, tools }) })
    permission.value = result; chosen.value = result.tools.map(key); baseline.value = JSON.stringify(chosen.value); error.value = ''; updates.value = false
    ElMessage.success('此生活线的工具授权已保存')
  } catch (cause) { error.value = errorText(cause) }
  finally { saving.value = false }
}
watch(() => props.refreshKey, () => { updates.value = true })
watch(() => props.instanceId, () => { version++; permission.value = null; chosen.value = []; baseline.value = '[]'; void load() })
onMounted(load); onScopeDispose(() => { version++ })
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <section class="panel" v-loading="loading">
    <div class="section-toolbar"><h2>此生活线的外部工具</h2><el-button :type="updates ? 'primary' : 'default'" :disabled="saving" @click="load">{{ updates ? '有变化 · 重新载入' : '重新载入' }}</el-button></div>
    <el-form label-position="top" :disabled="saving || loading" @submit.prevent="save">
      <el-form-item label="允许模型调用（最多 16 项）"><el-select v-model="chosen" multiple filterable clearable collapse-tags collapse-tags-tooltip :max-collapse-tags="3" :multiple-limit="16" placeholder="默认不授权任何外部工具"><el-option-group v-for="server in available" :key="server.id" :label="server.name"><el-option v-for="item in server.choices" :key="item.value" :label="item.label" :value="item.value" /></el-option-group><el-option v-for="value in missing" :key="value" :value="value" label="已失效工具 · 请移除" /></el-select></el-form-item>
      <el-empty v-if="catalog && !available.some(server => server.choices.length)" description="暂无可授权工具"><a href="#mcp">配置 MCP 连接并同步目录</a></el-empty>
      <div class="form-actions"><el-tag v-if="dirty" effect="plain" type="warning">未保存</el-tag><span class="action-spacer"></span><el-button type="primary" native-type="submit" :loading="saving" :disabled="!permission || !dirty || !!missing.length">保存授权</el-button></div>
    </el-form>
    <el-collapse class="quiet-collapse"><el-collapse-item title="授权变更的影响" name="impact">只作用于当前生活线。保存变更会取消旧运行和尚未发送的计划；连接或目录变化后须重新授权。</el-collapse-item></el-collapse>
  </section>
  <section class="panel section-gap table-scroll"><h2>最近调用</h2><el-table :data="calls" empty-text="此生活线尚无外部调用"><el-table-column prop="tool_name" label="工具" min-width="160" /><el-table-column label="运行" width="90"><template #default="{ row }">#{{ row.run_id }}</template></el-table-column><el-table-column label="状态" min-width="100"><template #default="{ row }">{{ mcpStatus(row.status) }}</template></el-table-column><el-table-column label="时间" min-width="170"><template #default="{ row }">{{ formatTime(row.created_at, timezone) }}</template></el-table-column><el-table-column label="详情" width="80"><template #default="{ row }"><el-button link @click="detail = row; visible = true">查看</el-button></template></el-table-column></el-table></section>
  <el-drawer v-model="visible" title="外部工具结果" size="min(720px, 94vw)" append-to-body><template v-if="detail"><p>{{ detail.tool_name }} · {{ mcpStatus(detail.status) }}</p><el-alert v-if="detail.error" :title="detail.error" type="warning" :closable="false" class="form-alert" /><DataDetails :value="detail.result_json" decode /></template></el-drawer>
</template>
