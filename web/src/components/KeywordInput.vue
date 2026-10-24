<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { ElInput } from 'element-plus'
const props = withDefaults(defineProps<{ label?: string; limit?: number }>(), { label: '关键词', limit: 64 })
const values = defineModel<string[]>({ required: true })
const pending = ref(''), error = ref('')
const field = ref<InstanceType<typeof ElInput> | null>(null)
watch(error, async value => { await nextTick(); field.value?.input?.setCustomValidity(value) })
watch(values, () => { error.value = '' }, { deep: true })
function add(text = pending.value) {
  const words = text.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
  const merged = [...new Set([...values.value, ...words])]
  if (merged.length > props.limit || words.some(word => word.length > 200)) { error.value = `最多 ${props.limit} 个关键词，每个不超过 200 字。`; return }
  values.value = merged; pending.value = ''; error.value = ''
}
function enter(event: Event | KeyboardEvent) { if (event instanceof KeyboardEvent && event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); add() } }
function paste(event: ClipboardEvent) { const text = event.clipboardData?.getData('text') ?? ''; if (text.includes('\n')) { event.preventDefault(); add(text) } }
</script>
<template><div class="keyword-input"><el-tag v-for="(word, index) in values" :key="word" closable effect="plain" @close="values.splice(index, 1)">{{ word }}</el-tag><el-input ref="field" v-model="pending" :aria-label="label" placeholder="输入后按回车，可粘贴多行" maxlength="200" @input="error = ''" @keydown="enter" @paste="paste" @blur="add()" /><p v-if="error" class="check-error" role="alert">{{ error }}</p></div></template>
