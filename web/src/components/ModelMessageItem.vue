<script setup lang="ts">
import DataDetails from './DataDetails.vue'
defineProps<{ message: { index: number; role: string; content: Record<string, unknown>[] }; initialOpen?: boolean }>()
const names: Record<string, string> = { system: '系统', user: '用户／运行资料', assistant: '助手', tool: '工具' }
</script>
<template>
  <details class="model-message" :open="initialOpen">
    <summary><el-tag effect="plain" size="small">{{ message.index + 1 }} · {{ names[message.role] || message.role }}</el-tag><span class="muted">{{ message.content.length }} 个内容段</span></summary>
    <div v-for="(part, index) in message.content" :key="index"><p v-if="part.type === 'text'" class="message-text">{{ part.text }}</p><DataDetails v-else :value="part" /></div>
  </details>
</template>
