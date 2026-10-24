<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import type { EvidenceRef, EvidenceRecord } from '../information-types'
import DataDetails from './DataDetails.vue'
import ConversationWindowLink from './ConversationWindowLink.vue'
const props = defineProps<{ instanceId: string; sources: EvidenceRef[]; timezone: string }>()
const names = { input: '用户输入', sent: '确认送达', life: '生活事件', world_event: '已采纳世界事件', observation: '公开观察' }
const visible = ref(false), value = ref<EvidenceRecord | null>(null), error = ref('')
let version = 0
async function show(source: EvidenceRef) {
  const mine = ++version; visible.value = true; value.value = null; error.value = ''
  try { const row = await api<EvidenceRecord>(`/information/instances/${props.instanceId}/evidence/${source.kind}/${source.id}`); if (mine === version) value.value = row }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
}
watch(() => props.instanceId, () => { version++; visible.value = false; value.value = null })
onScopeDispose(() => { version++ })
</script>
<template>
  <div class="inline-actions"><el-button v-for="source in sources" :key="`${source.kind}-${source.id}`" link @click="show(source)">{{ names[source.kind] }} #{{ source.id }}</el-button></div>
  <el-drawer v-model="visible" title="原始来源" size="min(760px, 94vw)" class="detail-drawer" @closed="version++">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-else-if="value"><p>{{ names[value.kind] }} #{{ value.id }} · {{ formatTime(value.at, timezone) }}</p><ConversationWindowLink :instance-id="instanceId" :source="value" :timezone="timezone" :disabled="!!value.validity?.invalid || value.deleted" /><el-alert v-if="value.validity?.invalid" :title="'来源已失效：' + value.validity.reason" type="warning" :closable="false" /><p>{{ value.meaning }}</p><p class="message-text">{{ value.text }}</p><DataDetails v-if="value.state_changes" :value="value.state_changes" /></template>
    <el-skeleton v-else :rows="4" animated />
  </el-drawer>
</template>
