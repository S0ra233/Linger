<script setup lang="ts">
import { computed, ref } from 'vue'
import SplitPane from './SplitPane.vue'
import type { PresetContent, PromptBlock } from '../preset-types'
const props = defineProps<{ templates?: PromptBlock[] }>()
const config = defineModel<PresetContent>({ required: true })
const form = computed(() => config.value.context)
const positions = [{ id: 'before_character', label: '角色资料前' }, { id: 'after_character', label: '角色资料后' }, { id: 'after_history', label: '历史后' }] as const
function neighbour(index: number, delta: number) {
  const list = form.value.prompt_blocks, item = list[index]
  if (!item) return -1
  for (let next = index + delta; next >= 0 && next < list.length; next += delta) if (list[next]?.position === item.position) return next
  return -1
}
function move(index: number, delta: number) { const list = form.value.prompt_blocks, other = neighbour(index, delta); if (other >= 0) [list[index], list[other]] = [list[other]!, list[index]!] }
function groupItems(position: PromptBlock['position']) { return form.value.prompt_blocks.filter(item => item.position === position) }
const tab = ref('prompts'), selected = ref('main'), dragging = ref('')
const block = computed(() => form.value.prompt_blocks.find(item => `block:${item.id}` === selected.value))
function add() { const id = crypto.randomUUID(); form.value.prompt_blocks.push({ id, name: '', role: 'system', position: 'before_character', content: '', enabled: true, run_types: [] }); selected.value = `block:${id}` }
function addFromTemplate(id: string) {
  if (id === 'blank') { if (form.value.prompt_blocks.length < 64) add(); return }
  const template = props.templates?.find(item => item.id === id)
  if (!template) return
  const existing = form.value.prompt_blocks.find(item => item.id === id)
  // 用户已编辑过的同名模板只启用并定位，不用内置文字覆盖。
  if (existing) existing.enabled = true
  else {
    if (form.value.prompt_blocks.length >= 64) return
    form.value.prompt_blocks.push({ ...template, run_types: [...template.run_types], enabled: true })
  }
  selected.value = `block:${id}`
}
function drop(id: string) {
  const list = form.value.prompt_blocks, from = list.findIndex(item => item.id === dragging.value), to = list.findIndex(item => item.id === id)
  // 拖动只改变同一插入位置内的顺序，跨位置必须显式修改字段。
  if (from >= 0 && to >= 0 && from !== to && list[from]?.position === list[to]?.position) list.splice(to, 0, list.splice(from, 1)[0]!)
  dragging.value = ''
}
function remove() { const index = form.value.prompt_blocks.findIndex(item => `block:${item.id}` === selected.value); if (index >= 0) form.value.prompt_blocks.splice(index, 1); selected.value = 'main' }
const blockIndex = computed(() => form.value.prompt_blocks.findIndex(item => `block:${item.id}` === selected.value))
</script>
<template>
  <el-tabs v-model="tab" class="editor-tabs">
    <el-tab-pane label="提示词" name="prompts">
      <SplitPane label="调整提示词列表宽度" :selection="selected" detail-label="提示词正文"><template #list="{ open: openDetail }">
        <div class="section-toolbar"><strong>提示词段</strong><el-dropdown v-if="templates?.length" trigger="click" @command="(id: string) => { addFromTemplate(id); openDetail() }"><el-button>添加 ⌄</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="blank" :disabled="form.prompt_blocks.length >= 64">空白提示词</el-dropdown-item><el-dropdown-item v-for="item in templates" :key="item.id" :command="item.id" :disabled="form.prompt_blocks.length >= 64 && !form.prompt_blocks.some(block => block.id === item.id)">{{ item.name }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown><el-button v-else :disabled="form.prompt_blocks.length >= 64" @click="add(); openDetail()">添加</el-button></div>
        <div class="editor-list">
          <button type="button" :class="['editor-list-item', { active: selected === 'main' }]" @click="selected = 'main'; openDetail()"><strong>系统提示词</strong><span>主指令</span></button>
          <button type="button" :class="['editor-list-item', { active: selected === 'post' }]" @click="selected = 'post'; openDetail()"><strong>历史后提示词</strong><span>对话后的补充指令</span></button>
          <button type="button" :class="['editor-list-item', { active: selected === 'tool' }]" @click="selected = 'tool'; openDetail()"><strong>工具提交提醒</strong><span>{{ form.tool_submission_hint?.trim() ? '已启用' : '已关闭' }}</span></button>
          <section v-for="group in positions" :key="group.id" class="prompt-group">
            <h3>{{ group.label }}</h3>
            <p v-if="!groupItems(group.id).length" class="muted">无附加段</p>
            <div v-for="item in groupItems(group.id)" :key="item.id" class="prompt-list-row" :class="{ dragging: dragging === item.id }" @dragover.prevent @drop.prevent="drop(item.id)">
              <button type="button" draggable="true" :class="['editor-list-item', { active: selected === `block:${item.id}` }]" @dragstart="dragging = item.id" @dragend="dragging = ''" @click="selected = `block:${item.id}`; openDetail()"><strong>{{ item.name || '未命名提示词' }}</strong><span>{{ item.enabled ? '已启用' : '已停用' }}</span></button>
            </div>
          </section>
        </div>
      </template>
      <el-form-item v-if="selected === 'main'" label="系统提示词"><el-input v-model="form.role_prompt" type="textarea" :autosize="{ minRows: 20 }" maxlength="8000" /></el-form-item>
      <el-form-item v-else-if="selected === 'post'" label="历史后提示词"><el-input v-model="form.post_history_prompt" type="textarea" :autosize="{ minRows: 16 }" maxlength="8000" /></el-form-item>
      <el-form-item v-else-if="selected === 'tool'" label="工具提交提醒（原生提交）"><el-input v-model="form.tool_submission_hint" type="textarea" :autosize="{ minRows: 6 }" maxlength="1000" placeholder="留空关闭" /></el-form-item>
      <template v-else-if="block">
        <div class="section-toolbar"><el-switch v-model="block.enabled" active-text="启用" /><div><el-button link :disabled="neighbour(blockIndex, -1) < 0" @click="move(blockIndex, -1)">上移</el-button><el-button link :disabled="neighbour(blockIndex, 1) < 0" @click="move(blockIndex, 1)">下移</el-button><el-button link type="danger" @click="remove">移除</el-button></div></div>
        <el-form-item label="名称"><el-input v-model="block.name" maxlength="200" /></el-form-item>
        <el-form-item label="适用运行（不选则全部）"><el-checkbox-group v-model="block.run_types"><el-checkbox value="input">普通回复</el-checkbox><el-checkbox value="life">后台生活</el-checkbox><el-checkbox value="summary">历史摘要</el-checkbox></el-checkbox-group></el-form-item>
        <div class="form-two-columns"><el-form-item label="消息角色"><el-select v-model="block.role"><el-option value="system" label="系统" /><el-option value="user" label="用户提示" /><el-option value="assistant" label="助手示例" /></el-select></el-form-item><el-form-item label="插入位置"><el-select v-model="block.position"><el-option value="before_character" label="角色资料前" /><el-option value="after_character" label="角色资料后" /><el-option value="after_history" label="历史后" /></el-select></el-form-item></div>
        <el-form-item label="内容"><el-input v-model="block.content" type="textarea" :autosize="{ minRows: 18 }" maxlength="16000" /></el-form-item>
      </template><el-empty v-else description="选择提示词" />
      </SplitPane>
    </el-tab-pane>
    <el-tab-pane label="生成参数" name="budget">
      <el-form-item label="期望推理强度"><el-select v-model="config.reasoning_effort"><el-option value="inherit" label="沿用模型条目" /><el-option :value="null" label="不发送" /><el-option v-for="effort in ['none','minimal','low','medium','high','xhigh','max','ultra']" :key="effort" :value="effort" :label="effort" /></el-select></el-form-item>
      <div class="form-two-columns"><el-form-item label="最大输出 tokens"><el-input-number v-model="config.max_output_tokens" :min="1" :max="1000000" /></el-form-item><el-form-item label="温度"><el-input-number v-model="config.temperature" :min="0" :max="10" :step="0.1" :value-on-clear="null" /></el-form-item></div>
      <el-collapse class="quiet-collapse"><el-collapse-item title="摘要用途补充指令" name="summary"><el-form-item label="作为摘要模型时使用"><el-input v-model="form.summary_instructions" type="textarea" :autosize="{ minRows: 8 }" maxlength="4000" /></el-form-item></el-collapse-item><el-collapse-item title="旧版上下文值（只读兼容）" name="legacy"><p class="field-hint">移出和保留规则请在独立上下文策略页配置；未绑定策略的旧对话仍兼容以下值。</p><pre class="diagnostic-json">{{ JSON.stringify(Object.fromEntries(Object.entries(form).filter(([key]) => !['role_prompt','post_history_prompt','tool_submission_hint','prompt_blocks','summary_instructions'].includes(key))), null, 2) }}</pre></el-collapse-item></el-collapse>
    </el-tab-pane>
  </el-tabs>
</template>
