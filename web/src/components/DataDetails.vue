<script setup lang="ts">
import { computed, ref } from 'vue'
const props = withDefaults(defineProps<{ value: unknown; depth?: number; decode?: boolean }>(), { depth: 0, decode: false })
const limit = ref(20)
const data = computed(() => { if (props.decode && typeof props.value === 'string') { try { return JSON.parse(props.value) as unknown } catch { /* 普通文本照原文展示。 */ } } return props.value })
const entries = computed(() => data.value && typeof data.value === 'object' ? Object.entries(data.value) : [])
const labels: Record<string, string> = {
  followups: '短期分支', deliveries: '长期投递', after_seconds: '回复后秒数', plan_kind: '计划类型', plan_id: '长期投递编号', plan_revision: '计划修订', anchor_at: '计时锚点', minimum_gap: '最小间隔', at: '安排时间', pending: '待处理', recent: '最近投递结果', held: '待复核',
  tool_execution_status: '工具结果状态', tools_completed: '已记录工具结果数', aborted_before_tool: '中断前待处理工具',
  attempts: '联网尝试', tracked: '已记录尝试', started: '开始次数',
  intent_changes: '意图变更', intent_id: '意图编号', intent_ids: '意图编号', due_at: '触发时间（Unix 秒）', trigger_kind: '运行来源', automatic: '自动唤醒', source_run_id: '来源运行', run_id: '运行编号', planned_bubbles: '计划气泡数', outbox_id: '投递编号', confirmation_source: '确认来源', triggers: '到期节点', context: '本轮资料', policy: '生活线开关', settings: '生活设置', settings_revision: '设置修订', policy_revision: '生活线修订', recent_life: '近期生活片段', event_id: '生活事件编号', minimum_next_wakeup_at: '最早下次唤醒（Unix 秒）', background_contact_allowed: '允许主动联系', unanswered_contacts: '连续未回复联系轮次', wake_blocked: '唤醒限制', contact_blocked: '主动联系限制', incoming_gap_seconds: '新输入间隔（秒）', idle_seconds: '无输入时长（秒）', silence_stage: '静默阶段', truncated: '已裁剪', next_before: '后续分页编号',
  life_fragment: '生活片段', state_changes: '状态变化', read_state: '内部已读判断', send_at: '计划发送时间', character_adjustment_proposal: '角色调整提案', source_message_ids: '来源输入编号', operation: '操作', expires_at: '过期时间（Unix 秒）', name: '名称', description: '描述', personality: '性格', scenario: '场景', dialogue_examples: '对话示例', first_message: '开场白', style: '对话风格', nickname: '称呼', system_prompt: '角色系统提示词', post_history_instructions: '历史后提示词', alternate_greetings: '备用开场白', entries: '条目', content: '正文', text: '内容', title: '标题', keys: '关键词', secondary_keys: '次级关键词', enabled: '启用', constant: '常驻', revision: '版本', id: '编号', status: '状态', error: '错误', role: '消息角色', message: '消息', messages: '消息', reply_mode: '回复方式', state: '角色状态', state_patch: '状态修改', read: '已读', delay_seconds: '延迟（秒）', deliver_at: '发送时间', memory_candidates: '记忆候选', evidence_message_ids: '来源消息', next_wakeup_at: '下次唤醒', bubbles: '气泡', input_tokens: '输入 tokens', output_tokens: '输出 tokens', total_tokens: '总 tokens', cached_input_tokens: '缓存输入 tokens', estimated_tokens: '估算 tokens', reason: '原因', selected: '已选取', skipped: '已跳过', trace: '筛选记录', before: '角色资料前', after: '角色资料后', memory: '记忆', knowledge: '知识', profile: '长期画像', temporary: '临时事件', episode: '情景记忆', source: '来源', model: '模型', usage: '用量', scan_complete: '扫描完整', query: '查询', context_window: '上下文窗口', metadata: '元数据', position: '位置', insertion_order: '插入顺序', priority: '优先级', scan_depth: '扫描消息数', token_budget: '预算 tokens', case_sensitive: '区分大小写', selective_logic: '次级逻辑', attachment_id: '附件编号', mime_type: '媒体类型', query_tail_limited: '查询已截取末尾', available_input_tokens: '可用输入 tokens', input_bytes: '输入字节', byte_limit: '字节上限', pending_inputs: '待处理输入', historical_messages: '历史消息数', output_reserve: '输出预留', prefix_hash: '前缀指纹', over_budget: '超出预算', fixed_over_budget: '固定设定超出预算', can_summarize: '可摘要', would_summarize: '预计摘要', window: '窗口', worldbooks: '世界书', information: '召回记录', summary_id: '摘要编号', estimated_input_tokens: '输入估算 tokens',
}
Object.assign(labels, { resolved_reasoning: '实际强度参数', parts: '消息片段', parts_json: '消息片段', asset_id: '素材编号', available_media: '可用素材', min_output_tokens: '最小输出上限', max_output_tokens: '最大输出上限', max_thinking_tokens: '最大思考预算' })
Object.assign(labels, { run_type: '本轮类型', run_types: '适用运行', prompt_blocks: '附加提示词采用情况', worldbook_name: '世界书', worldbook_id: '世界书编号', entry_id: '条目编号', primary_hits: '命中的主关键词', secondary_hits: '命中的次级关键词' })
Object.assign(labels, { scene: '当前场景', location: '地点', activity: '活动', participants: '在场者', unfinished: '未完事项', sources: '来源', meaning: '来源语义', retrieval_query: '本轮检索词', retrieval_basis: '检索依据', limited: '范围已限制', history_incomplete: '历史尚未读完', pending_overflow: '输入积压超限', pending_total: '待处理总数', summary_batches: '已归纳批次', summary_max_batches: '摘要批次上限', pending_input_limit: '单轮输入上限', previous_summary_id: '前一摘要编号', input_ids: '输入来源编号', sent_ids: '送达来源编号', at: '记录时间（Unix 秒）' })
Object.assign(labels, { topic: '话题', abandon_if: '失效条件', commitment_source: '承诺来源', deadline_at: '最终失效时间（Unix 秒）', evaluations: '已用到期评估次数', max_evaluations: '到期评估上限', resolution: '评估结果', outcome_reason: '结果原因', resolution_run_id: '决定来源运行', legacy: '尚无完整话题身份' })
const complex = (value: unknown) => value !== null && typeof value === 'object'
const count = (value: unknown) => Array.isArray(value) ? `${value.length} 项` : `${Object.keys(value as object).length} 个字段`
function scalar(value: unknown, key?: string) {
  if (key === 'resolution' && typeof value === 'string') {
    const names: Record<string, string> = { evaluating: '正在评估', deferred: '延期', edited: '调整', abandoned: '放弃', evaluated: '已评估', contact_planned: '决定联系，待投递回执', expired: '过期', failed: '失败', cancelled: '取消', interrupted: '中断', source_invalid: '来源失效' }
    return names[value] ?? value
  }
  if (key === 'run_types' && Array.isArray(value)) {
    const names: Record<string, string> = { input: '普通回复', life: '后台生活', summary: '历史摘要' }
    return value.length ? value.map(item => names[String(item)] ?? String(item)).join('、') : '全部'
  }
  if ((key === 'reason' || key === 'run_type') && typeof value === 'string') {
    const names: Record<string, string> = { input: '普通回复', life: '后台生活', summary: '历史摘要', selected: '已采用', matched: '已采用', disabled: '已停用', empty: '正文为空', run_type: '不适用本轮', no_primary_keys: '未设主关键词', primary_miss: '主关键词未命中', secondary_miss: '次级条件未满足', total_budget: '总预算不足', book_budget: '本书预算不足' }
    return names[value] ?? value
  }
  if (key === 'tool_execution_status' && typeof value === 'string') {
    const states: Record<string, string> = { unsettled: '尚未结算', completed: '已处理', interrupted: '处理已中断', not_requested: '未请求工具' }
    return states[value] ?? value
  }
  return value === null || value === undefined ? '未提供' : value === true ? '是' : value === false ? '否' : String(value) || '空'
}
</script>
<template>
  <pre v-if="depth >= 7" class="diagnostic-json">{{ JSON.stringify(data, null, 2) }}</pre>
  <div v-else-if="complex(data)" class="data-details" :class="{ 'data-array': Array.isArray(data) }">
    <p v-if="!entries.length" class="muted">{{ Array.isArray(data) ? '暂无条目' : '暂无字段' }}</p>
    <div v-for="([key, value], index) in entries.slice(0, limit)" :key="key" class="data-field">
      <details v-if="complex(value) && key !== 'run_types'"><summary>{{ Array.isArray(data) ? `第 ${index + 1} 项` : labels[key] || key }} <span>{{ count(value) }}</span></summary><DataDetails :value="value" :depth="depth + 1" /></details>
      <template v-else><dt>{{ Array.isArray(data) ? index + 1 : labels[key] || key }}</dt><dd>{{ scalar(value, key) }}</dd></template>
    </div>
    <el-button v-if="entries.length > limit" link @click="limit += 20">继续展开（余 {{ entries.length - limit }} 项）</el-button>
  </div>
  <p v-else class="message-text">{{ scalar(data) }}</p>
</template>
