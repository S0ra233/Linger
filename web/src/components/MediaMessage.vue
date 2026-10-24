<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ parts: string | undefined }>()
const assets = computed<{ asset_id: string; kind: string; name: string }[]>(() => {
  try { const value = JSON.parse(props.parts ?? '[]'); return Array.isArray(value) ? value.filter(item => item.type === 'asset') : [] } catch { return [] }
})
</script>
<template><div v-if="assets.length" class="media-message"><div v-for="(item, index) in assets" :key="index"><img v-if="['image', 'sticker'].includes(item.kind)" :src="`/api/v1/media/${item.asset_id}/preview`" :alt="item.name" loading="lazy" /><span>{{ item.name }}</span></div></div></template>
