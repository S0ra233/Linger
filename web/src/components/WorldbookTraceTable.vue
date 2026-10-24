<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { WorldbookTrace } from '../material-types'
import DataDetails from './DataDetails.vue'

const props = defineProps<{ items: WorldbookTrace[] }>()
const query = ref(''), selectedOnly = ref(false), page = ref(1)
const reasons: Record<string, string> = {
  matched: '已采用', disabled: '已停用', empty: '正文为空', run_type: '不适用本轮',
  no_primary_keys: '未设主关键词', primary_miss: '主关键词未命中', secondary_miss: '次级条件未满足',
  total_budget: '总预算不足', book_budget: '本书预算不足', budget: '预算不足',
  sticky: '持续采用', cooldown: '冷却中', delay: '延迟启用中',
}
const filtered = computed(() => props.items.filter(item => (!selectedOnly.value || item.selected) &&
  `${item.worldbook_name || ''} ${item.name} ${item.entry_id}`.toLocaleLowerCase().includes(query.value.toLocaleLowerCase())))
const rows = computed(() => filtered.value.slice((page.value - 1) * 20, page.value * 20))
watch([() => props.items, query, selectedOnly], () => { page.value = 1 })
</script>

<template>
  <div class="section-toolbar"><el-input v-model="query" clearable placeholder="筛选世界书与条目" aria-label="筛选世界书与条目" class="account-select" /><el-checkbox v-model="selectedOnly">仅已采用</el-checkbox><span>{{ filtered.length }} / {{ items.length }} 条</span></div>
  <el-empty v-if="!filtered.length" description="没有匹配条目" :image-size="60" />
  <template v-else>
    <div class="table-scroll"><el-table :data="rows">
      <el-table-column type="expand"><template #default="{ row }"><DataDetails :value="row" /></template></el-table-column>
      <el-table-column label="条目" min-width="140"><template #default="{ row }">{{ row.name || row.entry_id }}</template></el-table-column>
      <el-table-column label="世界书" min-width="140"><template #default="{ row }">{{ row.worldbook_name || row.worldbook_id }} · v{{ row.revision }}</template></el-table-column>
      <el-table-column label="结果" min-width="140"><template #default="{ row }"><el-tag :type="row.selected ? 'success' : 'info'" effect="plain">{{ reasons[row.reason] || row.reason }}</el-tag></template></el-table-column>
      <el-table-column prop="estimated_tokens" label="估算 tokens" width="115" />
    </el-table></div>
    <el-pagination v-if="filtered.length > 20" v-model:current-page="page" :page-size="20" :total="filtered.length" layout="prev, pager, next" class="section-gap" />
  </template>
</template>
