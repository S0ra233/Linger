<script setup lang="ts">
import { nextTick, onScopeDispose, ref, watch } from 'vue'
import { editorCount, mayLeave, useEditorClose } from '../ui-state'
const props = defineProps<{ title: string; draft: unknown; busy?: boolean; guardChildren?: boolean; scope?: string; version?: number | null; plain?: boolean }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ closed: [] }>()
const heading = ref<HTMLElement | null>(null)
const { allowLeave } = useEditorClose(open, () => !!props.busy)
async function close() { if (await (props.guardChildren ? mayLeave() : allowLeave())) open.value = false }
let counted = false, disposed = false, returnFocus: HTMLElement | null = null, returnScroll = 0
watch(open, async value => {
  if (value && !counted) { counted = true; editorCount.value++; returnFocus = document.activeElement as HTMLElement; returnScroll = window.scrollY; await nextTick(); if (disposed || !counted || !open.value) return; heading.value?.focus(); window.scrollTo(0, 0) }
  if (!value && counted) { counted = false; editorCount.value--; emit('closed'); await nextTick(); if (disposed || counted || open.value) return; if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true }); window.scrollTo({ top: returnScroll }) }
}, { immediate: true, flush: 'post' })
onScopeDispose(() => { disposed = true; if (counted) { counted = false; editorCount.value-- } })
defineExpose({ close })
</script>
<template>
  <Teleport to="#editor-host">
    <Transition name="workspace">
      <section v-if="open" :class="['editor-workspace', { 'editor-workspace-plain': plain }]" :aria-label="title" :aria-busy="busy || undefined">
        <header class="editor-heading"><div class="editor-title"><h1 ref="heading" tabindex="-1">{{ title }}</h1><p v-if="scope" class="editor-scope">{{ scope }}</p></div><div class="editor-heading-actions"><el-button :disabled="busy" @click="close">← 返回</el-button><slot name="heading" /></div></header>
        <fieldset class="editor-content" :disabled="busy"><slot :close="close" /></fieldset>
      </section>
    </Transition>
  </Teleport>
</template>
