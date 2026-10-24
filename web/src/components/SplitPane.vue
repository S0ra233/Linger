<script setup lang="ts">
import { nextTick, onScopeDispose, ref, watch } from 'vue'
const props = withDefaults(defineProps<{ label?: string; initial?: number; selection?: string | number; detailLabel?: string }>(), { label: '调整列表宽度', initial: 28, detailLabel: '编辑内容' })
const ratio = ref(props.initial), container = ref<HTMLElement | null>(null)
const detailOpen = ref(false), content = ref<HTMLElement | null>(null), list = ref<HTMLElement | null>(null)
let listScroll = 0, listPage = 0
async function openDetail() {
  if (!detailOpen.value) { listScroll = list.value?.querySelector('.editor-list')?.scrollTop ?? 0; listPage = window.scrollY }
  detailOpen.value = true
  if (window.matchMedia('(max-width: 900px)').matches) { await nextTick(); container.value?.scrollIntoView({ block: 'start' }); content.value?.focus({ preventScroll: true }) }
}
async function showList() {
  detailOpen.value = false; await nextTick()
  const scroll = list.value?.querySelector('.editor-list'); if (scroll) scroll.scrollTop = listScroll
  window.scrollTo({ top: listPage })
  list.value?.querySelector<HTMLElement>('.editor-list-item.active')?.focus({ preventScroll: true })
}
watch(() => props.selection, () => { void openDetail() })
let endDrag: (() => void) | null = null
function start(event: PointerEvent) {
  if (event.button !== 0) return
  event.preventDefault(); endDrag?.()
  const target = event.currentTarget as HTMLElement
  target.setPointerCapture(event.pointerId)
  const move = (value: PointerEvent) => { const box = container.value?.getBoundingClientRect(); if (box) ratio.value = Math.max(22, Math.min(46, (value.clientX - box.left) / box.width * 100)) }
  const end = () => { target.removeEventListener('pointermove', move); target.removeEventListener('pointerup', end); target.removeEventListener('lostpointercapture', end); endDrag = null }
  endDrag = end; target.addEventListener('pointermove', move); target.addEventListener('pointerup', end); target.addEventListener('lostpointercapture', end)
}
function key(event: KeyboardEvent) {
  if (['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) { event.preventDefault(); ratio.value = event.key === 'Home' ? props.initial : Math.max(22, Math.min(46, ratio.value + (event.key === 'ArrowLeft' ? -2 : 2))) }
}
onScopeDispose(() => endDrag?.())
</script>
<template><div ref="container" :class="['split-pane', { 'detail-open': detailOpen }]" :style="{ '--list-width': `${ratio}%` }"><aside ref="list" class="split-list"><slot name="list" :open="openDetail" /></aside><div class="split-handle" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="label" :aria-valuenow="Math.round(ratio)" :aria-valuemin="22" :aria-valuemax="46" @pointerdown="start" @dblclick="ratio = initial" @keydown="key"></div><section ref="content" tabindex="-1" class="split-content" :aria-label="detailLabel"><div class="split-mobile-heading"><el-button @click="showList">← 返回列表</el-button><strong>{{ detailLabel }}</strong></div><slot /></section></div></template>
