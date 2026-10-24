<script setup lang="ts">
import { computed } from 'vue'
import NavIcon from './NavIcon.vue'
defineProps<{ title: string; disabled?: boolean; icon?: string }>()
const open = defineModel<boolean>({ default: false })
const expanded = computed({ get: () => open.value ? ['section'] : [], set: (value: (string | number)[] | string | number) => { open.value = Array.isArray(value) ? value.includes('section') : value === 'section' } })
</script>

<template>
  <el-collapse v-model="expanded" class="settings-section">
    <el-collapse-item name="section" :disabled="disabled">
      <template #title><span v-if="icon" class="settings-section-icon" aria-hidden="true"><NavIcon :name="icon" /></span><span class="settings-section-title">{{ title }}</span><span class="settings-section-action" aria-hidden="true">{{ open ? '收起' : '展开' }}</span></template>
      <slot />
    </el-collapse-item>
  </el-collapse>
</template>
