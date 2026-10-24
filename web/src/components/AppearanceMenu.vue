<script setup lang="ts">
import { ref } from 'vue'
import { appearance } from '../appearance'
import NavIcon from './NavIcon.vue'
defineProps<{ compact?: boolean }>()
const visible = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
function close() { visible.value = false; trigger.value?.focus() }
</script>

<template>
  <el-popover v-model:visible="visible" trigger="click" :placement="compact ? 'bottom-end' : 'right-end'" :width="280" popper-class="appearance-popover" :show-arrow="false" :persistent="false" @after-enter="closeButton?.focus()">
    <template #reference><button ref="trigger" type="button" :class="compact ? 'appearance-trigger' : 'sidebar-utility'" :aria-expanded="visible" aria-label="外观与材质"><NavIcon name="appearance" /><span v-if="!compact">外观</span></button></template>
    <section aria-label="外观选项" @keydown.esc.stop="close">
    <div class="appearance-heading"><strong>外观</strong><button ref="closeButton" type="button" class="icon-button" aria-label="关闭外观" @click="close"><NavIcon name="close" /></button></div>
    <div class="theme-options" role="group" aria-label="配色">
      <button v-for="theme in (['blue', 'warm'] as const)" :key="theme" type="button" :class="['theme-option', theme]" :aria-pressed="appearance.theme === theme" @click="appearance.theme = theme"><span class="theme-sample" aria-hidden="true"></span><span>{{ theme === 'blue' ? '浅蓝' : '暖白' }}</span><NavIcon v-if="appearance.theme === theme" name="check" /></button>
    </div>
    <div class="appearance-row"><label for="rp-glass">玻璃质感</label><el-switch id="rp-glass" v-model="appearance.glass" /></div>
    <div class="appearance-row"><label for="rp-reduce-motion">减少动态效果</label><el-switch id="rp-reduce-motion" v-model="appearance.reduceMotion" /></div>
    </section>
  </el-popover>
</template>
