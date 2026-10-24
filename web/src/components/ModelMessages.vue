<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import ModelMessageItem from './ModelMessageItem.vue'
const props = defineProps<{ messages: unknown[] }>()
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' ? value as Record<string, unknown> : {} }
const rows = computed(() => props.messages.map((value, index) => {
  const row = record(value)
  return { index, role: String(row.role ?? '未知'), layer: row.context_layer,
    content: Array.isArray(row.content) ? row.content.map(record) : [{ type: 'text', text: row.content }] }
}))
type Row = typeof rows.value[number]
const blocks = computed(() => {
  const result: { key: number; message?: Row; turns?: Row[][] }[] = []
  for (const row of rows.value) {
    if (row.layer !== 'history') { result.push({ key: row.index, message: row }); continue }
    let block = result[result.length - 1]
    if (!block?.turns) { block = { key: row.index, turns: [] }; result.push(block) }
    const turns = block.turns!, previous = turns[turns.length - 1]
    // 与服务端完整轮次相同：连续输入及随后多个确认气泡放在一起。
    if (!previous || row.role === 'user' && previous.some(item => item.role === 'assistant')) turns.push([row])
    else previous.push(row)
  }
  return result
})
const pages = reactive<Record<number, { open: boolean; page: number; size: number }>>({})
watch(blocks, values => {
  for (const key of Object.keys(pages)) delete pages[Number(key)]
  for (const block of values) if (block.turns) pages[block.key] = { open: false, page: Math.max(1, Math.ceil(block.turns.length / 30)), size: 30 }
}, { immediate: true })
function visible(key: number, turns: Row[][]) { const state = pages[key]!; return turns.slice((state.page - 1) * state.size, state.page * state.size) }
function resize(key: number, count: number) { const state = pages[key]!; state.page = Math.max(1, Math.ceil(count / state.size)) }
</script>
<template>
  <div class="model-messages">
    <template v-for="block in blocks" :key="block.key">
      <ModelMessageItem v-if="block.message" :message="block.message" :initial-open="block.key < 2" />
      <details v-else-if="block.turns && pages[block.key]" class="conversation-fold" @toggle="pages[block.key]!.open = ($event.target as HTMLDetailsElement).open">
        <summary>近期对话 <el-tag effect="plain" size="small">{{ block.turns.length }} 轮</el-tag></summary>
        <template v-if="pages[block.key]!.open">
          <div class="conversation-pager"><el-pagination v-model:current-page="pages[block.key]!.page" v-model:page-size="pages[block.key]!.size" :page-sizes="[20, 30, 50]" :total="block.turns.length" :pager-count="5" layout="sizes, prev, pager, next" @size-change="resize(block.key, block.turns!.length)" /></div>
          <section v-for="(turn, offset) in visible(block.key, block.turns)" :key="turn[0]!.index" class="conversation-turn">
            <div class="conversation-turn-label">第 {{ (pages[block.key]!.page - 1) * pages[block.key]!.size + offset + 1 }} 轮</div>
            <ModelMessageItem v-for="message in turn" :key="message.index" :message="message" initial-open />
          </section>
        </template>
      </details>
    </template>
  </div>
</template>
