<script setup lang="ts">
import { onMounted, onScopeDispose, ref, watch } from 'vue'
import { api, errorText, formatTime } from '../api'
import DataDetails from './DataDetails.vue'
interface Group { id: number; instance_id: string; created_at: number; input: string; preview: string; cancelled_count: number }
interface Item { id: string; text: string; due_at: number; status: string; error: string | null; plan_kind: string; parts_json: string; after_seconds: number | null; anchor_at: number | null }
const props = defineProps<{ instanceId?: string; refreshKey: number; timezone: string }>()
const rows = ref<Group[]>([]), details = ref<Record<number, Item[]>>({}), expanded = ref<string[]>([])
const page = ref(1), total = ref(0), loading = ref(false), pending = ref(new Set<number>()), error = ref(''), stale = ref(false)
const drawer = ref(false), raw = ref<unknown>(null)
let version = 0
const labels: Record<string, string> = { cancelled: '未发送 · 已回收', succeeded: '已发送', pending: '待发送', sending: '发送中', unknown: '结果待确认', failed: '发送失败' }
async function load(target = page.value) {
  const mine = ++version; loading.value = true
  try {
    const value = await api<{ items: Group[]; total: number }>(`/roleplay/life/recycle?page=${target}${props.instanceId ? '&instance_id=' + encodeURIComponent(props.instanceId) : ''}`)
    if (mine !== version) return
    if (!value.items.length && target > 1 && value.total > 0) { void load(Math.ceil(value.total / 20)); return }
    rows.value = value.items; total.value = value.total; page.value = target
    details.value = {}; pending.value.clear(); stale.value = false; error.value = ''
    await expand()
  } catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
async function expand() {
  const mine = version
  for (const id of expanded.value.map(Number).filter(id => rows.value.some(row => row.id === id) && !details.value[id])) {
    if (mine !== version) return
    if (pending.value.has(id)) continue
    pending.value.add(id)
    try { const value = await api<Item[]>(`/roleplay/life/recycle/${id}`); if (mine === version) details.value[id] = value }
    catch (cause) { if (mine === version) error.value = errorText(cause) }
    finally { if (mine === version) pending.value.delete(id) }
  }
}
watch(() => props.instanceId, () => { expanded.value = []; drawer.value = false; void load(1) })
// 父页显式刷新后同步重读回收；保留当前页和展开项，不要求用户再次刷新。
watch(() => props.refreshKey, () => { stale.value = true; void load() })
onMounted(() => load()); onScopeDispose(() => { version++ })
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <div class="section-toolbar"><span>未发送计划</span><el-button :loading="loading" @click="load()">{{ stale ? '有更新 · 刷新' : '刷新' }}</el-button></div>
  <el-empty v-if="!loading && !rows.length" description="还没有回收的计划" />
  <el-collapse v-model="expanded" class="quiet-collapse" @change="expand">
    <el-collapse-item v-for="row in rows" :key="row.id" :name="String(row.id)">
      <template #title><div class="recycle-heading"><span>{{ row.preview }}</span><span class="check-metadata">{{ formatTime(row.created_at, timezone) }} · {{ row.cancelled_count }} 条回收</span></div></template>
      <p v-if="row.input" class="recycle-input">你：{{ row.input }}</p>
      <el-skeleton v-if="pending.has(row.id)" :rows="2" animated />
      <article v-for="item in details[row.id] ?? []" :key="item.id" class="recycle-message" :class="{ 'was-sent': item.status === 'succeeded' }">
        <div class="section-toolbar"><el-tag :type="item.status === 'succeeded' ? 'success' : item.status === 'unknown' ? 'warning' : 'info'" effect="plain">{{ labels[item.status] || item.status }}</el-tag><span>{{ item.plan_kind === 'followup' && item.anchor_at === null ? `原定回复后 ${item.after_seconds} 秒` : formatTime(item.due_at, timezone) }}</span><el-button link @click="raw = item; drawer = true">详情</el-button></div>
        <p class="message-text">{{ item.text }}</p><p v-if="item.error" class="check-metadata">{{ item.error }}</p>
      </article>
    </el-collapse-item>
  </el-collapse>
  <el-pagination v-if="total > 20" class="section-gap" :current-page="page" :page-size="20" :total="total" layout="prev, pager, next" :disabled="loading" @current-change="(value: number) => load(value)" />
  <el-drawer v-model="drawer" title="计划详情" size="min(640px, 100vw)" append-to-body><DataDetails :value="raw" /></el-drawer>
</template>

<style scoped>
.recycle-heading { display: flex; flex: 1; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 18px 12px 0; min-width: 0; }
.recycle-heading > span:first-child { overflow-wrap: anywhere; }
.recycle-input { padding: 12px 16px; border-radius: 12px; background: var(--el-fill-color-light); white-space: pre-wrap; }
.recycle-message { margin: 12px 0; padding: 16px; border: 1px solid var(--el-border-color-lighter); border-radius: 12px; }
.recycle-message.was-sent { background: var(--el-fill-color-lighter); }
.message-text { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 640px) { .recycle-heading { align-items: flex-start; flex-direction: column; } }
</style>
