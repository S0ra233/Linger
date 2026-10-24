<script setup lang="ts">
import { computed } from 'vue'
import type { RunDiagnostics, RunIssueCode } from '../runtime-types'

const props = defineProps<{ value?: RunDiagnostics; status: string }>()
const emit = defineEmits<{ locate: [stepId: number] }>()
const stages = {
  setup: '运行准备', context: '上下文准备', provider: '模型请求', response: '响应接收',
  tools: '工具与计划校验', cleanup: '关闭运行资源', commit: '计划提交', runtime: '运行协调',
}
const codes: Record<RunIssueCode, string> = {
  configuration: '配置不满足', context_invalid: '上下文准备失败', context_budget: '上下文超预算',
  provider_request: '模型请求失败', empty_response: '模型返回空响应', output_limit: '输出超过限制', incomplete_response: '响应未完整结束',
  tool_identity: '调用编号异常', tool_limit: '工具数量超限', tool_json: '工具参数JSON损坏', tool_arguments: '工具参数不合格',
  plan_json: '计划JSON损坏', plan_validation: '计划校验未通过', tool_execution: '工具执行失败', missing_plan: '未调用计划工具', tool_selection: '接口工具约束未遵守',
  repair_exhausted: '修复后仍不合格', step_limit: '模型步骤用尽', commit_validation: '提交校验未通过',
  stale_run: '旧运行已失效', cancelled: '运行已取消', timeout: '超过总时限', internal: '未分类异常',
}
const hints: Partial<Record<RunIssueCode, string>> = {
  configuration: '在模型连接中核对主叙事用途、启用状态、提示词预设及凭据。',
  context_budget: '查看下方预算拦截记录，在联系人上下文中调整窗口或保留规则。',
  provider_request: '查看对应步骤的接口错误和尝试记录。',
  empty_response: '接口已返回，但没有可用的最终内容。查看尝试记录中的空响应补偿与用量；缺失用量保持未知。',
  repair_exhausted: '展开下方问题，比较首次返回与修复返回；不会自动增加请求。',
  commit_validation: '计划结构已通过前序校验，请核对提交时的来源、期限或状态限制。',
  stale_run: '输入或配置发生变化，旧计划未提交；待处理输入仍保留。',
}
const deliveryLabels = {
  pending: '待发送', sending: '发送中', succeeded: '已送达', failed: '失败',
  cancelled: '已取消', unknown: '结果未知', expired: '已过期',
}
const deliveries = computed(() => Object.entries(props.value?.deliveries ?? {})
  .filter(([, count]) => count > 0)
  .map(([status, count]) => ({ status, count, label: deliveryLabels[status as keyof typeof deliveryLabels] })))
</script>

<template>
  <section v-if="value" class="run-diagnostics">
    <div class="section-toolbar">
      <h3>运行定位</h3>
      <el-tag effect="plain" :type="status === 'completed' ? 'success' : 'info'">
        {{ status === 'completed' ? '本次计划已提交' : status === 'running' ? '运行中' : '未提交计划' }}
      </el-tag>
    </div>
    <template v-if="value.failure">
      <el-alert :title="value.failure.message" type="warning" :closable="false" />
      <div class="section-toolbar section-gap">
        <span>{{ stages[value.failure.stage] }} · {{ codes[value.failure.code] }}<template v-if="value.failure.tool"> · {{ value.failure.tool }}</template></span>
        <el-button v-if="value.failure.step_id" link @click="emit('locate', value.failure.step_id)">
          定位步骤 {{ value.failure.step }}
        </el-button>
      </div>
      <p v-if="value.failure.stage_elapsed_ms != null" class="muted">终止前所在阶段已历时 {{ (value.failure.stage_elapsed_ms / 1000).toFixed(1) }} 秒<span v-if="value.failure.step != null"> · 步骤 {{ value.failure.step }}</span></p>
      <p v-if="hints[value.failure.code]" class="muted">{{ hints[value.failure.code] }}</p>
      <p v-for="(field, index) in value.failure.fields" :key="index" class="message-text">
        {{ field.path.join('.') || '参数整体' }}：{{ field.message }}
      </p>
    </template>
    <el-alert v-if="value.long_plan_recovery?.restored" class="section-gap" type="info" :closable="false"
      :title="`本轮失败时恢复了 ${value.long_plan_recovery.restored} 项既有长期安排。`" />
    <p v-if="deliveries.length" class="delivery-counts">
      <span v-for="item in deliveries" :key="item.status">{{ item.label }} {{ item.count }}</span>
    </p>
    <p v-if="value.deliveries.unknown" class="muted">未知投递不会自动重发，请在投递计划中核对 QQ 记录。</p>
    <details v-if="value.steps.length" class="raw-details section-gap">
      <summary>返回与修复问题 · {{ value.steps.length }} 个步骤</summary>
      <article v-for="step in value.steps" :key="step.step_id" class="issue-step">
        <div class="section-toolbar">
          <strong>步骤 {{ step.step }} · {{ step.repair_attempt == null ? '修复轮未记录' : step.repair_attempt ? '修复返回 ' + step.repair_attempt : '首次校验' }}</strong>
          <el-button link @click="emit('locate', step.step_id)">定位步骤</el-button>
        </div>
        <div v-for="(issue, index) in step.issues" :key="index" class="issue-item">
          <p>{{ codes[issue.code] }}<span v-if="issue.tool"> · {{ issue.tool }}</span></p>
          <p class="message-text">{{ issue.message }}</p>
          <p v-for="(field, fieldIndex) in issue.fields" :key="fieldIndex" class="message-text muted">
            {{ field.path.join('.') || '参数整体' }}：{{ field.message }}
          </p>
        </div>
      </article>
    </details>
    <p v-if="!value.recorded && status !== 'completed' && status !== 'running'" class="muted">本记录没有结构化定位信息，请查看原错误和步骤；不根据旧错误文字推断原因。</p>
  </section>
</template>

<style scoped>
.run-diagnostics { margin: 24px 0; }
.delivery-counts { display: flex; flex-wrap: wrap; gap: 8px 20px; }
.issue-step { padding: 16px 0; }
.issue-step + .issue-step { border-top: 1px solid var(--el-border-color-lighter); }
.issue-item p { margin: 8px 0; }
</style>
