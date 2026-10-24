<script setup lang="ts">
import type { RunExplanation } from '../runtime-types'
import { formatTime } from '../api'
import DataDetails from './DataDetails.vue'
defineProps<{ value?: RunExplanation; timezone: string }>()
const modes: Record<string, string> = { cost: '成本优先', continuity: '连续性优先', legacy: '兼容模式' }
const layers: Record<string, string> = { system: '系统与工具', preset: '模型预设', world: '世界书', character: '角色卡', profile: '用户与关系资料', state: '生活资料', retrieval: '检索快照', summary: '摘要', history: '近期历史', input: '本轮输入', runtime: '尾部运行资料' }
const reasons: Record<string, string> = { rounds: '达到轮数阈值', history_tokens: '近期历史达到Token阈值', capacity: '整体容量触发', materials: '设定或资料变化', legacy_summary: '兼容模式摘要' }
const actions: Record<string, string> = { 'memory.saved': '管理员修改记忆', 'memory.protection': '确认／解锁记忆', 'memory.superseded': '事实替代', 'memory.proposal_reviewed': '审核记忆提案', 'evidence.status': '来源状态变更', 'conversation.deleted': '删除最近轮次', 'context.rules_saved': '上下文规则变更', 'life.extensions_saved': '生活能力变更', 'instance.character_synced': '同步角色设定' }
const token = (value: number | null | undefined) => value == null ? '未知' : value.toLocaleString()
</script>
<template>
  <section v-if="value" class="run-explanation">
    <div class="section-toolbar"><h3>本轮上下文与缓存</h3><el-tag effect="plain" :type="value.adopted ? 'success' : 'info'">{{ value.adopted ? '计划已采纳' : '未成为当前有效计划' }}</el-tag></div>
    <el-alert v-if="value.memory_outcomes?.some(item => item.status === 'queue_full')" title="待审记忆队列已满：部分候选仅保留在下方已提交计划中，未修改锁定事实；聊天投递不受此项阻断。请先处理记忆页的待审提案。" type="warning" :closable="false" class="form-alert" />
    <p v-if="value.transition">近期历史 {{ value.transition.before_turns }} → {{ value.transition.after_turns }} 轮；移出 {{ value.transition.input_removed }} 条输入、{{ value.transition.sent_removed }} 个送达气泡<span v-if="value.transition.input_removed || value.transition.sent_removed">，{{ reasons[value.transition.reason ?? ''] ?? value.transition.reason ?? '原因未记录' }}</span>。</p>
    <p v-else class="muted">未记录完整的历史整理结果<span v-if="value.planning.reason">；当时触发：{{ reasons[value.planning.reason] ?? value.planning.reason }}</span>。</p>
    <p v-if="value.transition && !value.adopted" class="muted">以上是准备阶段的候选范围，未据此认定工作集已更新。</p>
    <dl class="detail-list overview-details"><div><dt>已知输入</dt><dd>{{ token(value.usage.input_tokens) }} tokens · {{ value.usage.reported_input_requests }}/{{ value.usage.requests }} 步报告</dd></div><div><dt>已知输出</dt><dd>{{ token(value.usage.output_tokens) }} tokens · {{ value.usage.reported_output_requests }}/{{ value.usage.requests }} 步报告</dd></div><div><dt>接口报告命中</dt><dd><el-tooltip :content="`仅对配对的 ${value.usage.paired_cache_requests} 个步骤计算：命中 ${token(value.usage.cache_read_tokens)} / 输入 ${token(value.usage.paired_input_tokens)}；不代表所有尝试均已报告用量。`"><span tabindex="0">{{ value.usage.cache_ratio == null ? '未知' : (value.usage.cache_ratio * 100).toFixed(2) + '%' }}</span></el-tooltip></dd></div><div><dt>计划修复轮</dt><dd>{{ value.usage.repair_attempts == null ? '未记录' : value.usage.repair_attempts }}</dd></div><div><dt>首个逻辑变化</dt><dd>{{ value.trace?.has_baseline ? value.trace.compatible === false ? '连接或生成配置不相容' : layers[value.first_changed_layer ?? ''] ?? '比较范围内未变' : '缺少可对照的前缀记录' }}</dd></div><div><dt>逻辑未变前缀</dt><dd>{{ token(value.estimated_reusable_prefix_tokens) }} tokens（粗估）</dd></div></dl>
    <details class="raw-details"><summary>版本与变化依据</summary>
      <dl v-if="value.versions" class="detail-list"><div><dt>角色</dt><dd>{{ value.versions.character.name }} · v{{ value.versions.character.revision }} · 局部调整 v{{ value.versions.character.adjustment_revision }}</dd></div><div><dt>模型预设</dt><dd>{{ value.versions.preset.id ? value.versions.preset.id.slice(0, 8) + ' · v' + value.versions.preset.revision : '模型独立配置' }}{{ value.versions.preset.local_overrides ? ' · 有本地修改' : '' }}</dd></div><div><dt>上下文策略</dt><dd>{{ modes[value.versions.strategy.mode] ?? value.versions.strategy.mode }}{{ value.versions.strategy.id ? ' · ' + value.versions.strategy.id.slice(0, 8) + ' v' + value.versions.strategy.revision : ' · 未绑定策略库' }} · 规则 v{{ value.versions.strategy.rule_revision }}</dd></div><div><dt>世界书引用</dt><dd>{{ value.versions.character.worldbooks.map(item => item.id.slice(0, 8) + ' v' + item.revision).join('、') || '未绑定' }}</dd></div></dl>
      <p v-else class="muted">本运行未保存角色／策略版本快照，不使用当前配置补造。</p>
      <p v-if="value.baseline_run_id">前缀对照来自已采纳运行 #{{ value.baseline_run_id }}。</p>
      <p v-else-if="value.trace?.has_baseline" class="muted">对照运行编号未记录或已失效，保留已有前缀比较依据。</p>
      <p v-if="value.trace?.changed_layers.length">有变化的逻辑层：{{ value.trace.changed_layers.map(item => layers[item] ?? item).join('、') }}。历史追加和输入变化也会列出。</p>
      <template v-if="value.transition"><p v-if="value.transition.retrieval_before != null">检索工作集 {{ value.transition.retrieval_before }} → {{ value.transition.retrieval_after }} 块，粗估 {{ token(value.transition.retrieval_before_tokens) }} → {{ token(value.transition.retrieval_after_tokens) }} tokens。</p><p>本次轻摘要 {{ value.transition.summary_batches }} 批。</p></template>
      <details v-if="value.version_changes.length" class="raw-details"><summary>具体版本变化</summary><article v-for="item in value.version_changes" :key="item.label"><strong>{{ item.label }}</strong><DataDetails :value="{ before: item.before, after: item.after }" /></article></details>
      <template v-if="value.operations.length"><p>两次准备之间记录的本线操作（仅时间范围关联）：</p><p v-for="item in value.operations" :key="item.id">{{ formatTime(item.created_at, timezone) }} · {{ actions[item.action] ?? item.action }}</p><p v-if="value.operations_limited" class="muted">仅列最近20项。</p></template>
      <p class="muted">{{ value.boundary }}</p>
    </details>
  </section>
</template>
<style scoped>
.run-explanation { margin: 24px 0; }
.run-explanation > p { line-height: 1.7; }
</style>
