<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref } from 'vue'
import { api, errorText, formatTime } from '../api'
import ConversationHistory from '../components/ConversationHistory.vue'
import NavIcon from '../components/NavIcon.vue'
interface Archive { id: string; instance_id: string; created_at: number; contact_name: string; character_name: string; rounds: number; input_count: number; sent_count: number; from_at: number; to_at: number }
interface ArchivePage { items: Archive[]; total: number; page: number; page_size: number }
const props = defineProps<{ timezone: string }>()
const search = ref(''), selected = ref(''), page = ref(1), loading = ref(false), error = ref('')
const result = ref<ArchivePage | null>(null), options = ref<{ id: string; label: string }[]>([])
const detail = ref<Archive | null>(null), detailOpen = ref(false)
let version = 0, disposed = false
const groups = computed(() => {
  const grouped = new Map<string, { label: string; items: Archive[] }>()
  for (const item of result.value?.items ?? []) {
    const key = item.instance_id, label = `${item.contact_name} · ${item.character_name}`
    if (!grouped.has(key)) grouped.set(key, { label, items: [] })
    grouped.get(key)!.items.push(item)
  }
  return [...grouped.entries()]
})
async function load(targetPage = 1) {
  const mine = ++version, query = new URLSearchParams({ page: String(targetPage), q: search.value })
  if (selected.value) query.set('instance_id', selected.value)
  loading.value = true; error.value = ''
  try { const value = await api<ArchivePage>(`/roleplay/conversation-archives?${query}`); if (mine === version) { result.value = value; page.value = value.page } }
  catch (cause) { if (mine === version) error.value = errorText(cause) }
  finally { if (mine === version) loading.value = false }
}
function open(item: Archive) { detail.value = item; detailOpen.value = true }
onMounted(async () => {
  void load()
  try {
    const rows = await api<{ id: string; display_name: string; nickname: string; peer_id: string; character_name: string }[]>('/roleplay/instances')
    if (!disposed) options.value = rows.map(row => ({ id: row.id, label: `${row.display_name || row.nickname || row.peer_id} · ${row.character_name}` }))
  } catch { /* 列表仍可查看与搜索，不让筛选选项加载阻断归档。 */ }
})
onScopeDispose(() => { disposed = true; version++ })
</script>
<template>
  <div class="archive-page">
    <div class="archive-heading"><h3>已归档的对话</h3><el-button :loading="loading" @click="load(page)">刷新</el-button></div>
    <div class="archive-filters"><el-input v-model="search" placeholder="搜索联系人或角色" clearable aria-label="搜索归档" maxlength="100" @keyup.enter="load()" @clear="load()" /><el-select v-model="selected" clearable filterable placeholder="所有联系人与角色" @change="load()"><el-option v-for="option in options" :key="option.id" :label="option.label" :value="option.id" /></el-select><el-button @click="load()">搜索</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <el-skeleton v-if="loading && !result" :rows="4" animated />
    <el-empty v-else-if="!result?.items.length" description="暂无归档对话" />
    <section v-for="[id, group] in groups" :key="id" class="archive-group"><div class="archive-group-label"><NavIcon name="materials" /><strong>{{ group.label }}</strong><span>{{ group.items.length }} 份归档</span></div><div class="archive-list"><button v-for="item in group.items" :key="item.id" class="archive-row" @click="open(item)"><span class="archive-row-main"><strong>{{ item.rounds }} 轮对话</strong><span>{{ formatTime(item.from_at, timezone) }} — {{ formatTime(item.to_at, timezone) }}</span></span><span class="archive-row-meta"><time>{{ formatTime(item.created_at, timezone) }} 归档</time><span class="archive-view-label">查看 <NavIcon name="arrow-right" /></span></span></button></div></section>
    <el-pagination v-if="result && result.total > result.page_size" :current-page="page" :page-size="result.page_size" :total="result.total" :disabled="loading" :pager-count="5" layout="prev, pager, next" @current-change="load" />
    <el-drawer v-model="detailOpen" :title="detail ? `${detail.contact_name} · ${detail.character_name}` : '归档对话'" size="min(900px, 96vw)" class="detail-drawer" append-to-body destroy-on-close><template v-if="detail"><p class="field-hint">归档记录仅供查看，暂不支持恢复到当前对话。</p><ConversationHistory :key="detail.id" :instance-id="detail.instance_id" :archive-id="detail.id" :refresh-key="0" :timezone="timezone" expanded /></template></el-drawer>
  </div>
</template>
