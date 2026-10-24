<script setup lang="ts">
import { useEditorClose } from '../ui-state'
defineOptions({ inheritAttrs: false })
const props = defineProps<{ draft: unknown; busy?: boolean }>()
const open = defineModel<boolean>({ required: true })
const { close } = useEditorClose(open, () => !!props.busy)
defineExpose({ close })
</script>
<template>
  <el-dialog v-bind="$attrs" append-to-body :model-value="open" :before-close="() => close()" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" class="model-dialog">
    <fieldset class="dialog-fields" :disabled="busy" :inert="busy || undefined"><slot :close="close" /></fieldset>
  </el-dialog>
</template>
