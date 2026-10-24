<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { selectedLifeLine } from '../ui-state'
import DataDetails from '../components/DataDetails.vue'
import ModelMessages from '../components/ModelMessages.vue'
import WorldbookTraceTable from '../components/WorldbookTraceTable.vue'
import EvidenceSources from '../components/EvidenceSources.vue'
import ContextBindingPanel from '../components/ContextBindingPanel.vue'
import RecentCacheRatio from '../components/RecentCacheRatio.vue'
import ContextOccupancy from '../components/ContextOccupancy.vue'
import PlanContractDetails from '../components/PlanContractDetails.vue'
import type { Scene } from '../information-types'
import { api, errorText, formatTime } from '../api'
import type { ContextPreview, SummaryRecord } from '../material-types'
import type { RoleInstance } from '../roleplay-types'

const props = defineProps<{ refreshKey: number; timezone: string; embedded?: boolean; showBinding?: boolean; instanceId?: string }>()
const selected = computed({ get: () => props.instanceId ?? selectedLifeLine.value, set: (value: string) => { if (!props.instanceId) selectedLifeLine.value = value } })
const trimLabels: Record<string, string> = { rounds: '轮数达到阈值', history_tokens: '历史Token达到阈值', capacity: '整体预算达到阈值', materials: '前置资料变化' }
const layerLabels: Record<string, string> = { system: '系统与工具', preset: '预设', world: '世界书', character: '角色卡', profile: '用户资料', state: '生活状态', retrieval: '检索快照', summary: '轻摘要', history: '近期对话', input: '本轮输入', runtime: '本轮运行资料' }
const runType = ref<'input' | 'life'>('input')
const scene = ref<Scene | null>(null)
const currentScene = computed(() => preview.value?.report.scene || scene.value)
const stale = ref(false)
const error = ref(''), busy = ref(false), instances = ref<RoleInstance[]>([])
const preview = ref<ContextPreview | null>(null), previewError = ref(''), previewLoading = ref(false), summaries = ref<SummaryRecord[]>([])
const status: Record<string, string> = { active: '已采用版本', superseded: '已被替代', disabled: '手动停用', candidate: '候选', running: '生成中', discarded: '未采纳', invalidated: '来源变更 · 待重建' }
function summaryLabel(item: SummaryRecord) { return item.status === 'active' && preview.value?.report.policy?.strategy === 'cost' ? '已保存 · 当前模式不注入' : status[item.status] ?? item.status }
let loadVersion = 0, previewVersion = 0
const pretty = (value: unknown) => { if (typeof value === 'string') { try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value } } return JSON.stringify(value, null, 2) }
async function load() {
  const mine = ++loadVersion
  try {
    const rows = await api<RoleInstance[]>('/roleplay/instances')
    if (mine !== loadVersion) return
    instances.value = rows; error.value = ''
  } catch (cause) { if (mine === loadVersion) error.value = errorText(cause) }
}
async function history(id: string, mine: number) {
  try { const [values, current] = await Promise.all([api<SummaryRecord[]>(`/roleplay/context/instances/${id}/summaries`), api<Scene>(`/information/instances/${id}/scene`)]); if (mine === previewVersion && id === selected.value) { summaries.value = values; scene.value = current } }
  catch (cause) { if (mine === previewVersion) previewError.value = errorText(cause) }
}
async function inspect() {
  if (!selected.value || previewLoading.value) return
  const id = selected.value, mine = ++previewVersion
  stale.value = false; previewLoading.value = true; previewError.value = ''; preview.value = null
  await Promise.all([
    api<ContextPreview>(`/roleplay/context/instances/${id}/preview?run_type=${runType.value}`).then(value => { if (mine === previewVersion) preview.value = value }).catch(cause => { if (mine === previewVersion) previewError.value = errorText(cause) }),
    history(id, mine),
  ])
  if (mine === previewVersion) previewLoading.value = false
}
async function clear() {
  const id = selected.value
  if (!id || busy.value) return
  try { await ElMessageBox.confirm('停止使用此生活线的摘要，并取消尚未发送的计划。原始对话保留；开启自动摘要时，下次运行可能重新归纳。', '停止使用摘要', { confirmButtonText: '确认停用', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  busy.value = true
  try { await api(`/roleplay/context/instances/${id}/clear-summary`, { method: 'POST' }); if (id === selected.value) { preview.value = null; await history(id, ++previewVersion) }; ElMessage.success('已停止使用摘要') }
  catch (cause) { previewError.value = errorText(cause) }
  finally { busy.value = false }
}
watch(selected, id => { previewVersion++; previewLoading.value = false; preview.value = null; previewError.value = ''; summaries.value = []; scene.value = null; if (id) void history(id, previewVersion) }, { immediate: true })
watch(runType, () => { previewVersion++; previewLoading.value = false; preview.value = null; previewError.value = ''; stale.value = false })
watch(() => props.refreshKey, () => { if (preview.value) stale.value = true; void load() }); onMounted(load)
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><el-select v-if="!embedded" v-model="selected" placeholder="选择生活线" filterable class="account-select"><el-option v-for="item in instances" :key="item.id" :value="item.id" :label="`${item.display_name || item.nickname || item.peer_id} · ${item.character_name} · ${item.account_name}`" /></el-select><el-radio-group v-model="runType" aria-label="预览运行类型"><el-radio-button value="input">普通回复</el-radio-button><el-radio-button value="life">后台生活</el-radio-button></el-radio-group><el-button :disabled="!selected" :loading="previewLoading" @click="inspect">{{ stale ? '有变化 · 重新装配' : '查看模型输入' }}</el-button></div>
  <el-alert v-if="previewError" :title="previewError" type="error" :closable="false" class="form-alert" />
  <el-empty v-if="!selected" description="选择需要查看的生活线" />
  <ContextBindingPanel v-if="selected && showBinding !== false" :instance-id="selected" :refresh-key="refreshKey" @saved="preview = null" />
  <section v-if="currentScene && selected" class="panel section-gap"><div class="panel-heading"><h2>当前场景</h2><el-tag :type="currentScene.status === 'active' ? 'success' : 'info'" effect="plain">{{ currentScene.status === 'active' ? '有效' : currentScene.status === 'expired' ? '已过期 · 当前未知' : currentScene.status === 'source_invalid' ? '来源失效 · 当前未知' : '尚无投影' }}</el-tag></div><template v-if="currentScene.status !== 'unknown'"><p class="message-text">{{ currentScene.location }} {{ currentScene.activity }}</p><p v-if="currentScene.participants?.length">在场：{{ currentScene.participants.join('、') }}</p><p v-if="currentScene.unfinished?.length">未完：{{ currentScene.unfinished.join('；') }}</p><p v-if="currentScene.expires_at">有效至 {{ formatTime(currentScene.expires_at, timezone) }}</p><EvidenceSources v-if="currentScene.sources" :instance-id="selected" :sources="currentScene.sources" :timezone="timezone" /></template></section>
  <section v-if="selected && !preview" class="panel section-gap"><RecentCacheRatio :instance-id="selected" :refresh-key="refreshKey" :timezone="timezone" /></section>
  <el-skeleton v-if="previewLoading" :rows="4" animated />
  <template v-if="preview">
    <el-alert v-if="preview.report.history_incomplete || preview.report.pending_overflow" :title="preview.report.pending_overflow ? `待处理输入 ${preview.report.pending_total} 条，超过单轮上限；本预览只展示部分输入，实际运行会停止。` : preview.report.layers ? '工作集超过读取上限；本预览不完整，实际运行会停止。' : '历史尚未读完；本预览只展示当前页，实际运行须先分批摘要。'" type="warning" :closable="false" class="form-alert" />
    <section class="panel">
      <div class="panel-heading"><h2>上下文预算</h2><el-tag v-if="stale" type="warning" effect="plain">预览待更新</el-tag><el-tag v-else effect="plain" type="info">本次装配估算</el-tag></div>
      <el-alert v-if="preview.report.over_budget" :title="preview.report.fixed_over_budget ? '固定设定或当前输入超预算，历史摘要无法解决。' : '当前上下文超预算。'" type="warning" :closable="false" class="form-alert" />
      <ContextOccupancy :report="preview.report" />
      <PlanContractDetails :contract="preview.report.plan_contract" />
      <RecentCacheRatio :instance-id="selected" :refresh-key="refreshKey" :timezone="timezone" />
      <el-alert v-if="preview.report.history_rule_conflict" title="历史Token触发量小于受保护轮次占用；运行会停止，请提高触发量。" type="warning" :closable="false" class="form-alert" />
      <dl v-if="preview.report.layers" class="detail-list overview-details"><div><dt>近期轮次</dt><dd>{{ preview.report.history_turns }} → {{ preview.report.would_trim ? preview.report.retained_after_trim : preview.report.history_turns }}</dd></div><div><dt>下次移出</dt><dd>{{ preview.report.would_trim ? trimLabels[preview.report.trim_reason || ''] || preview.report.trim_reason : '暂不触发' }}</dd></div><div v-if="preview.report.would_trim"><dt>移出后输入（摘要按目标粗估）</dt><dd>约 {{ preview.report.after_trim_estimated_input_tokens?.toLocaleString() }} tokens</dd></div><div><dt>逻辑未变前缀（粗估）</dt><dd>{{ preview.report.estimated_reusable_prefix_tokens?.toLocaleString() }} tokens</dd></div><div><dt>首个变化位置</dt><dd>{{ preview.report.first_changed_layer ? layerLabels[preview.report.first_changed_layer] : '未变化' }}</dd></div></dl>
      <dl class="detail-list overview-details"><div><dt>预算参考</dt><dd>{{ preview.report.budget_input_tokens ?? preview.report.estimated_input_tokens }} tokens</dd></div><div><dt>可用输入</dt><dd>{{ preview.report.available_input_tokens == null ? '未配置窗口' : preview.report.available_input_tokens + ' tokens' }}</dd></div><div><dt>输出预留</dt><dd>{{ preview.report.output_reserve }} tokens</dd></div><div><dt>输入字节</dt><dd>{{ preview.report.input_bytes }} / {{ preview.report.byte_limit }}</dd></div><div><dt>历史／待处理</dt><dd>{{ preview.report.historical_messages }} / {{ preview.report.pending_inputs }} 条</dd></div><div><dt>摘要判断</dt><dd>{{ preview.report.would_summarize ? '预计触发摘要' : '不触发摘要' }}</dd></div></dl>
    </section>
    <section class="panel section-gap"><div class="panel-heading"><h2>装配消息</h2><span class="muted">{{ preview.messages.length }} 条 · 只读</span></div><ModelMessages :messages="preview.messages" /></section>
    <section class="panel section-gap"><el-alert v-if="preview.report.information?.semantic_not_executed" title="只读预览不执行检索工具、Embedding或摘要。" type="info" :closable="false" class="form-alert" /><el-collapse><el-collapse-item title="提示词采用情况" name="prompts"><DataDetails :value="preview.report.prompt_blocks" /></el-collapse-item><el-collapse-item title="本轮资料装配依据" name="basis"><p class="message-text">{{ preview.report.retrieval_query ? '本地匹配输入（不会自动发起搜索）：' + preview.report.retrieval_query : '尚无匹配输入' }}</p><DataDetails :value="preview.report.retrieval_basis" /></el-collapse-item><el-collapse-item title="记忆与知识召回" name="information"><DataDetails :value="preview.report.information" /></el-collapse-item><el-collapse-item title="世界书匹配与预算" name="books"><WorldbookTraceTable :items="preview.report.worldbooks.trace" /></el-collapse-item><el-collapse-item title="高级详情 · 原始预算记录" name="report"><pre class="diagnostic-json">{{ pretty(preview.report) }}</pre></el-collapse-item></el-collapse></section>
  </template>
  <section v-if="selected" class="panel section-gap"><div class="section-toolbar"><h2>历史摘要</h2><el-button v-if="summaries.some(item => item.status === 'active')" :disabled="busy" @click="clear">停止使用当前摘要</el-button></div><el-empty v-if="!summaries.length" description="还没有摘要记录" /><el-collapse v-else><el-collapse-item v-for="item in summaries" :key="item.id" :name="item.id" :title="`#${item.id} · ${summaryLabel(item)} · ${formatTime(item.created_at, timezone)}`"><p v-if="item.error" class="check-error">{{ item.error }}</p><div class="message-text">{{ item.text || '无摘要正文' }}</div><details class="raw-details"><summary>来源、模型与用量</summary><pre class="diagnostic-json">{{ pretty(item.source_json) }}</pre><pre class="diagnostic-json">{{ pretty(item.model_json) }}</pre><pre class="diagnostic-json">{{ pretty(item.usage_json) }}</pre></details></el-collapse-item></el-collapse></section>
</template>
