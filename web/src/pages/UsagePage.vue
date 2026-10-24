<script setup lang="ts">
import { computed, onMounted, onScopeDispose, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import { useEditorClose } from '../ui-state'
import DataDetails from '../components/DataDetails.vue'
import CostBreakdown from '../components/CostBreakdown.vue'
import type { RoleInstance } from '../roleplay-types'
import type { CacheComparison, CostParts } from '../usage-types'

interface Rule {
  connection_id: string; model_id: string; mode: 'flat' | 'read_discount' | 'split'
  input_per_million: string | null; output_per_million: string | null
  cache_read_per_million: string | null; cache_write_per_million: string | null
}
interface Prices { revision: number; currency: string; rules: Rule[]; updated_at: number | null }
interface Model { connection_id: string; model_id: string; connection_name?: string; model: string | null; protocol?: string | null; archived?: boolean }
interface Metric { known: number | null; reported: number; missing: number; applicable: number; total: number | null }
interface Totals {
  records: number; usage: Record<string, Metric>
  cost: { known: string | null; total: string | null; priced_records: number; missing_records: number; parts?: CostParts }
  cache_comparison?: CacheComparison
  attempts: { known: number | null; untracked_records: number; unreported: number }
  cache: { paired_records: number; ratio: number | null }
}
interface UsageRecord {
  origin: string; source_id: string | number; at: number; status: string; purpose: string
  connection_id: string | null; model_id: string | null; model: string | null; protocol: string | null
  instance_id: string | null; run_id: number | null; usage: Record<string, number | null>
  estimated_cost: string | null; cost_reason: string | null; attempts: number | null; unreported_attempts: number | null
  cost_parts?: CostParts | null; cache_comparison?: CacheComparison | null
}
interface Report {
  price_revision: number; currency: string; calculated_at: number; start: number; end: number
  summary: Totals; groups: (Model & Totals)[]; items: UsageRecord[]; page: number; has_more: boolean
}
const props = defineProps<{ refreshKey: number; timezone: string }>()
const prices = ref<Prices | null>(null), catalog = ref<Model[]>([]), report = ref<Report | null>(null)
const loading = ref(false), priceLoading = ref(false), saving = ref(false), error = ref(''), priceError = ref(''), changed = ref(false)
const range = ref<[Date, Date] | null>([new Date(Date.now() - 7 * 86400000), new Date()])
const purpose = ref(''), selection = ref(''), tab = ref('report'), detail = ref<UsageRecord | null>(null)
const editor = ref(false), currencyOnly = ref(false), editingModel = ref<Model | null>(null), editError = ref('')
const base = ref<Prices | null>(null)
const conversations = ref<RoleInstance[]>([]), instance = ref(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('instance') ?? '')
const form = reactive({ mode: 'read_discount' as Rule['mode'], currency: 'CNY', input_per_million: '', output_per_million: '', cache_read_per_million: '', cache_write_per_million: '' })
const guard = useEditorClose(editor, () => saving.value)
const appliedQuery = ref('')
const filtersDirty = computed(() => { try { return !!report.value && query(1).toString() !== appliedQuery.value } catch { return true } })
const labels: Record<string, string> = { reply: '私聊回复', life: '后台生活', summary: '历史摘要', check: '连接检查', embedding_document: '索引向量', embedding_query: '检索向量', cache_keepalive: '缓存保活' }
const modes: Record<string, string> = { flat: '输入统一计价', read_discount: '缓存读取单独计价', split: '缓存读写分别计价' }
const rateFields = ['input_per_million', 'output_per_million', 'cache_read_per_million', 'cache_write_per_million'] as const
let disposed = false, requestVersion = 0
const modelKey = (value: { connection_id: string; model_id: string }) => JSON.stringify([value.connection_id, value.model_id])
const models = computed(() => {
  const entries = new Map<string, Model>()
  for (const value of report.value?.groups ?? []) if (value.connection_id && value.model_id) entries.set(modelKey(value), { ...value })
  for (const rule of prices.value?.rules ?? []) if (!entries.has(modelKey(rule))) entries.set(modelKey(rule), { ...rule, model: null })
  for (const value of catalog.value) entries.set(modelKey(value), value)
  return [...entries.values()]
})
const stalePrice = computed(() => report.value && prices.value && report.value.price_revision !== prices.value.revision)
const isEmbedding = computed(() => editingModel.value?.protocol?.startsWith('embedding_') ?? false)
const ruleFor = (model: Model) => prices.value?.rules.find(rule => modelKey(rule) === modelKey(model))
function modelLabel(model: Model) { return `${model.connection_name ?? connectionLabel(model.connection_id)} · ${model.model ?? model.model_id}` }
function connectionLabel(id: string | null) { return catalog.value.find(model => model.connection_id === id)?.connection_name ?? id ?? '连接身份未知' }
function count(value: number | null | undefined) { return value == null ? '未知' : value.toLocaleString('zh-CN') }
function metric(value: Metric | undefined) { return value ? value.applicable === 0 ? '不适用' : `${count(value.known)}${value.missing ? `（缺 ${value.missing} 条）` : ''}` : '未知' }
function money(value: string | null | undefined, currency = report.value?.currency ?? prices.value?.currency ?? '') { return value == null ? '未知' : `${currency} ${value}` }
function query(page: number) {
  if (!range.value) throw new Error('请选择起止时间。')
  const params = new URLSearchParams({ start: String(range.value[0].getTime() / 1000), end: String(range.value[1].getTime() / 1000), page: String(page) })
  if (purpose.value) params.set('source', purpose.value)
  if (instance.value) params.set('instance_id', instance.value)
  if (selection.value) {
    const [connection, model] = JSON.parse(selection.value) as [string, string]
    params.set('connection_id', connection); params.set('model_id', model)
  }
  return params
}
async function loadReport(page = 1) {
  if (loading.value || disposed) return
  const mine = ++requestVersion
  loading.value = true; error.value = ''
  try {
    const params = query(page), applied = query(1).toString()
    const result = await api<Report>(`/usage?${params}`)
    if (!disposed && mine === requestVersion) { report.value = result; appliedQuery.value = applied; changed.value = false; detail.value = null }
  } catch (cause) { if (!disposed && mine === requestVersion) error.value = errorText(cause) }
  finally { if (mine === requestVersion) loading.value = false }
}
async function loadPrices() {
  if (editor.value || saving.value || priceLoading.value || disposed) return
  priceLoading.value = true
  try {
    const result = await api<{ prices: Prices; catalog: Model[] }>('/usage/prices')
    if (!disposed && !editor.value) { prices.value = result.prices; catalog.value = result.catalog; priceError.value = '' }
  } catch (cause) { if (!disposed) priceError.value = errorText(cause) }
  finally { priceLoading.value = false }
}
function recent(days: number) { range.value = [new Date(Date.now() - days * 86400000), new Date()]; void loadReport() }
function editPrice(model: Model | null) {
  if (!prices.value || saving.value || priceLoading.value) return
  base.value = JSON.parse(JSON.stringify(prices.value)) as Prices
  editingModel.value = model; currencyOnly.value = model === null
  const rule = model ? ruleFor(model) : undefined
  Object.assign(form, { mode: rule?.mode ?? (isEmbedding.value ? 'flat' : 'read_discount'), currency: prices.value.currency })
  for (const key of rateFields) form[key] = rule?.[key] ?? ''
  editError.value = ''; editor.value = true
}
function agreedPrices() {
  Object.assign(form, { mode: 'read_discount', input_per_million: '1', output_per_million: '4', cache_read_per_million: '0.02', cache_write_per_million: '' })
}
async function savePrice() {
  if (!base.value || saving.value) return
  saving.value = true; editError.value = ''
  try {
    const rules = [...base.value.rules]
    if (!currencyOnly.value && editingModel.value) {
      const model = editingModel.value
      const rule: Rule = { connection_id: model.connection_id, model_id: model.model_id, mode: form.mode,
        input_per_million: form.input_per_million.trim() || null, output_per_million: form.output_per_million.trim() || null,
        cache_read_per_million: form.cache_read_per_million.trim() || null, cache_write_per_million: form.cache_write_per_million.trim() || null }
      const index = rules.findIndex(value => modelKey(value) === modelKey(model))
      if (index < 0) rules.push(rule); else rules[index] = rule
    }
    const result = await api<Prices>('/usage/prices', { method: 'PUT', body: JSON.stringify({ revision: base.value.revision, currency: form.currency.trim().toUpperCase(), rules }) })
    if (!disposed) { prices.value = result; editor.value = false; changed.value = true; ElMessage.success('自设价格已保存，报表需重新统计') }
  } catch (cause) { if (!disposed) editError.value = errorText(cause) }
  finally { saving.value = false }
}
async function clearPrice(model: Model) {
  if (!prices.value || saving.value || priceLoading.value || editor.value) return
  const snapshot = prices.value
  saving.value = true
  try {
    await ElMessageBox.confirm('清除后，这个模型的费用估算将标记为未知。', '清除自设价格', { confirmButtonText: '清除', cancelButtonText: '取消' })
    const result = await api<Prices>('/usage/prices', { method: 'PUT', body: JSON.stringify({ revision: snapshot.revision, currency: snapshot.currency, rules: snapshot.rules.filter(rule => modelKey(rule) !== modelKey(model)) }) })
    if (!disposed) { prices.value = result; changed.value = true; ElMessage.success('已清除价格') }
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close' && !disposed) priceError.value = errorText(cause) }
  finally { saving.value = false }
}
watch(() => props.refreshKey, () => { changed.value = true })
onMounted(() => { void loadPrices(); void loadReport(); void api<RoleInstance[]>('/roleplay/instances').then(rows => { if (!disposed) conversations.value = rows }).catch(cause => { if (!disposed) error.value = errorText(cause) }) })
onScopeDispose(() => { disposed = true; requestVersion++ })
</script>

<template>
  <section class="panel">
    <div class="panel-heading"><h2>用量与估算</h2><el-button :disabled="loading || saving" @click="() => { loadPrices(); loadReport() }">刷新</el-button></div>
    <el-alert v-if="error || priceError" :title="[error, priceError].filter(Boolean).join('；')" type="error" :closable="false" class="form-alert" />
    <el-alert v-if="stalePrice || changed" :title="stalePrice ? '报表与已载入价格版本不同，请刷新核对。' : '有新的运行记录或配置，请重新统计。'" type="info" :closable="false" class="form-alert" />
    <el-alert v-if="filtersDirty" title="筛选已修改，点击统计后应用。" type="info" :closable="false" class="form-alert" />
    <el-tabs v-model="tab" class="management-tabs">
      <el-tab-pane label="用量报表" name="report">
        <div class="usage-filters">
          <el-date-picker v-model="range" type="datetimerange" start-placeholder="开始时间" end-placeholder="结束时间" :clearable="false" :disabled="loading" />
          <el-select v-model="purpose" placeholder="全部来源" clearable :disabled="loading"><el-option v-for="(label, value) in labels" :key="value" :label="label" :value="value" /></el-select>
          <el-select v-model="selection" placeholder="全部模型" clearable filterable :disabled="loading"><el-option v-for="model in models" :key="modelKey(model)" :label="modelLabel(model)" :value="modelKey(model)" /></el-select>
          <el-select v-model="instance" placeholder="全部对话" clearable filterable :disabled="loading"><el-option v-for="item in conversations" :key="item.id" :value="item.id" :label="`${item.display_name || item.nickname || item.peer_id} · ${item.character_name} · ${item.account_name}`" /></el-select>
          <el-button type="primary" :loading="loading" @click="loadReport()">统计</el-button>
        </div>
        <div class="usage-shortcuts"><el-button link :disabled="loading" @click="recent(1)">最近 24 小时</el-button><el-button link :disabled="loading" @click="recent(7)">7 天</el-button><el-button link :disabled="loading" @click="recent(30)">30 天</el-button><span>选时按浏览器时区，最长 31 天</span></div>
        <el-skeleton v-if="loading && !report" :rows="6" animated />
        <template v-if="report">
          <p class="usage-caption">{{ formatTime(report.start, timezone) }} — {{ formatTime(report.end, timezone) }} · 当前自设价格 v{{ report.price_revision }} · {{ report.currency }} · 不是厂商账单</p>
          <div class="overview-numbers usage-numbers">
            <div><span>调用记录</span><strong>{{ count(report.summary.records) }}</strong></div>
            <div><span>输入 Token 已知部分</span><strong>{{ count(report.summary.usage.input_tokens?.known) }}</strong></div>
            <div><span>输出 Token 已知部分</span><strong>{{ count(report.summary.usage.output_tokens?.known) }}</strong></div>
            <div><span>{{ report.summary.cost.total === null ? '可估记录小计' : '记录范围费用估算' }}</span><strong class="usage-money">{{ money(report.summary.cost.known) }}</strong></div>
          </div>
          <div class="usage-coverage"><span>费用未知 {{ report.summary.cost.missing_records }} 条</span><span>联网尝试已记录 {{ count(report.summary.attempts.known) }} 次</span><span>用量未报告的尝试 {{ report.summary.attempts.unreported }} 次</span><span>尝试数缺失 {{ report.summary.attempts.untracked_records }} 条</span><span>缓存读取比例 {{ report.summary.cache.ratio === null ? '未知' : (report.summary.cache.ratio * 100).toFixed(2) + '%' }}（{{ report.summary.cache.paired_records }} 条有效配对）</span></div>
          <el-alert v-if="report.summary.cost.total === null && report.summary.records" title="统计存在未知项；小计仅包含可计算响应，不代表全部调用费用。" type="warning" :closable="false" class="form-alert" />
          <template v-if="report.summary.records"><div class="panel-heading section-gap"><h3>已估响应费用构成</h3><el-tooltip content="按当前自设价格，只拆分用量与价格齐全的响应；占比是费用占比，不是 Token 占比。推理已包含在输出中。"><span class="help-dot" tabindex="0" aria-label="查看费用构成口径">?</span></el-tooltip></div><CostBreakdown :parts="report.summary.cost.parts" :currency="report.currency" :comparison="report.summary.cache_comparison" /></template>
          <el-empty v-if="!report.summary.records" description="所选范围没有调用记录" :image-size="70" />
          <template v-else>
            <div class="table-scroll section-gap"><el-table :data="report.groups">
              <el-table-column label="模型／历史快照" min-width="220"><template #default="{ row }">{{ row.model ?? row.model_id ?? '模型身份未知' }}<p class="check-metadata">{{ connectionLabel(row.connection_id) }} · {{ row.protocol ?? '历史协议未知' }}</p></template></el-table-column>
              <el-table-column prop="records" label="记录" width="80" />
              <el-table-column label="输入" min-width="150"><template #default="{ row }">{{ metric(row.usage.input_tokens) }}</template></el-table-column>
              <el-table-column label="输出（含推理）" min-width="150"><template #default="{ row }">{{ metric(row.usage.output_tokens) }}</template></el-table-column>
              <el-table-column label="缓存读取" min-width="150"><template #default="{ row }">{{ metric(row.usage.cache_read_tokens) }}</template></el-table-column>
              <el-table-column label="缓存写入" min-width="150"><template #default="{ row }">{{ metric(row.usage.cache_write_tokens) }}</template></el-table-column>
              <el-table-column label="可估部分" min-width="170"><template #default="{ row }">{{ money(row.cost.known) }}</template></el-table-column>
              <el-table-column label="价格" width="80"><template #default="{ row }"><el-button v-if="row.connection_id && row.model_id" link :disabled="saving || priceLoading || !prices" @click="editPrice(row)">设置</el-button></template></el-table-column>
            </el-table></div>
            <div class="panel-heading section-gap"><h3>来源记录</h3><span class="muted">第 {{ report.page }} 页 · 每页 50 条</span></div>
            <div class="table-scroll"><el-table :data="report.items">
              <el-table-column label="时间" min-width="175"><template #default="{ row }">{{ formatTime(row.at, timezone) }}</template></el-table-column>
              <el-table-column label="来源" min-width="150"><template #default="{ row }">{{ labels[row.purpose] ?? row.purpose }} #{{ row.source_id }}</template></el-table-column>
              <el-table-column prop="model" label="模型" min-width="160"><template #default="{ row }">{{ row.model ?? row.model_id ?? '未知' }}</template></el-table-column>
              <el-table-column label="响应估算" min-width="200"><template #default="{ row }">{{ money(row.estimated_cost) }}<p v-if="row.cost_reason" class="check-metadata">{{ row.cost_reason }}</p><p v-if="row.unreported_attempts || row.attempts === null" class="check-metadata">另有未知尝试用量</p></template></el-table-column>
              <el-table-column label="详情" width="80"><template #default="{ row }"><el-button link @click="detail = row">查看</el-button></template></el-table-column>
            </el-table></div>
            <div class="form-actions"><el-button :disabled="loading || filtersDirty || report.page <= 1" @click="loadReport(report.page - 1)">上一页</el-button><el-button :disabled="loading || filtersDirty || !report.has_more" @click="loadReport(report.page + 1)">下一页</el-button></div>
          </template>
        </template>
      </el-tab-pane>
      <el-tab-pane label="自设价格" name="prices">
        <div class="panel-heading"><span>{{ prices ? prices.currency + '／百万 Token' : '尚未载入' }}</span><div><el-button :disabled="!prices || saving || priceLoading" @click="editPrice(null)">修改币种</el-button><el-button :loading="priceLoading" :disabled="saving" @click="loadPrices">重新载入</el-button></div></div>
        <p class="usage-caption">留空表示未知，0 表示明确免费。修改后按新价格回算所选历史记录，不修改原始用量，不换汇。</p>
        <el-empty v-if="!models.length && !priceLoading && !priceError" description="先添加模型连接，或统计已有历史记录" :image-size="70" />
        <div v-if="models.length" class="table-scroll"><el-table :data="models">
          <el-table-column label="连接／模型条目" min-width="250"><template #default="{ row }">{{ modelLabel(row) }}<el-tag v-if="row.archived" size="small" effect="plain">归档</el-tag></template></el-table-column>
          <el-table-column label="计价方式" min-width="180"><template #default="{ row }">{{ ruleFor(row) ? modes[ruleFor(row)!.mode] : '未设置' }}</template></el-table-column>
          <el-table-column label="输出" min-width="100"><template #default="{ row }">{{ ruleFor(row)?.output_per_million ?? '未知' }}</template></el-table-column>
          <el-table-column label="命中缓存输入" min-width="140"><template #default="{ row }">{{ (ruleFor(row)?.mode === 'flat' ? ruleFor(row)?.input_per_million : ruleFor(row)?.cache_read_per_million) ?? '未知' }}</template></el-table-column>
          <el-table-column label="未命中缓存输入" min-width="150"><template #default="{ row }">{{ ruleFor(row)?.input_per_million ?? '未知' }}</template></el-table-column>
          <el-table-column label="操作" width="150"><template #default="{ row }"><el-button link :disabled="saving || priceLoading || !prices" @click="editPrice(row)">设置</el-button><el-button v-if="ruleFor(row)" link :disabled="saving || priceLoading" @click="clearPrice(row)">清除</el-button></template></el-table-column>
        </el-table></div>
      </el-tab-pane>
    </el-tabs>
  </section>
  <el-dialog :model-value="editor" :title="currencyOnly ? '统计币种' : '模型自设价格'" width="620px" :close-on-click-modal="false" :close-on-press-escape="!saving" :before-close="() => guard.close()">
    <el-alert v-if="editError" :title="editError" type="error" :closable="false" class="form-alert" />
    <el-form label-position="top" :disabled="saving" @submit.prevent="savePrice">
      <template v-if="currencyOnly"><el-form-item label="币种代码"><el-input v-model="form.currency" maxlength="3" placeholder="USD、CNY 等三位大写代码" /></el-form-item><el-alert title="只改变全部单价的计价单位，不转换数值。保存后需重新统计。" type="warning" :closable="false" /></template>
      <template v-else><p class="usage-caption">{{ editingModel ? modelLabel(editingModel) : '' }} · {{ base?.currency }}／百万 Token</p>
        <div class="form-actions" v-if="base?.currency === 'CNY' && !isEmbedding"><el-button link @click="agreedPrices">填入约定价格：4 / 0.02 / 1</el-button></div>
        <div class="form-two-columns"><el-form-item v-if="!isEmbedding" label="输出（含推理）"><el-input v-model="form.output_per_million" placeholder="留空为未知" /></el-form-item><el-form-item v-if="!isEmbedding" label="命中缓存输入"><el-input v-model="form.cache_read_per_million" :disabled="form.mode === 'flat'" :placeholder="form.mode === 'flat' ? '沿用统一输入单价' : '留空为未知'" /></el-form-item><el-form-item :label="isEmbedding ? '输入' : '未命中缓存输入'"><el-input v-model="form.input_per_million" placeholder="留空为未知" /></el-form-item></div>
        <el-collapse v-if="!isEmbedding"><el-collapse-item title="高级计价方式" name="billing"><el-form-item label="输入计价"><el-select v-model="form.mode"><el-option label="三项价格：输出、命中输入、未命中输入" value="read_discount" /><el-option label="输入统一计价（兼容已有规则）" value="flat" /><el-option label="缓存写入另收费" value="split" /></el-select></el-form-item><el-form-item v-if="form.mode === 'split'" label="缓存写入"><el-input v-model="form.cache_write_per_million" placeholder="留空为未知" /></el-form-item></el-collapse-item></el-collapse>
            </template>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="guard.close">取消</el-button><el-button type="primary" :loading="saving" @click="savePrice">保存价格设置</el-button></template>
  </el-dialog>
  <el-drawer :model-value="!!detail" title="用量来源" size="min(680px, 100vw)" class="detail-drawer" @close="detail = null">
    <template v-if="detail"><dl class="detail-list"><div><dt>原记录</dt><dd>{{ detail.origin }} #{{ detail.source_id }} · {{ detail.status }}</dd></div><div><dt>接口连接</dt><dd>{{ connectionLabel(detail.connection_id) }} · {{ detail.model_id ?? '条目未知' }}</dd></div><div><dt>模型快照</dt><dd>{{ detail.model ?? '未知' }} · {{ detail.protocol ?? '未知' }}</dd></div><div><dt>联网尝试</dt><dd>{{ count(detail.attempts) }} · 未报告用量 {{ count(detail.unreported_attempts) }}</dd></div><div><dt>响应估算</dt><dd>{{ money(detail.estimated_cost) }} {{ detail.cost_reason ?? '' }}</dd></div><div v-if="detail.run_id"><dt>所属运行</dt><dd>#{{ detail.run_id }} <a v-if="detail.instance_id" class="action-link" :href="`#runtime?instance=${encodeURIComponent(detail.instance_id)}`">查看生活线运行</a></dd></div></dl><CostBreakdown :parts="detail.cost_parts" :currency="report?.currency ?? ''" :comparison="detail.cache_comparison" /><DataDetails :value="detail.usage" /></template>
  </el-drawer>
</template>
