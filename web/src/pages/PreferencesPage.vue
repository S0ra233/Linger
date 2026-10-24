<script setup lang="ts">
import { computed, onMounted, onScopeDispose, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { protectPage } from '../ui-state'
import { api, errorText } from '../api'
import type { Preferences } from '../api'

const props = defineProps<{ refreshKey: number }>()
const emit = defineEmits<{ saved: [value: Preferences] }>()
const form = reactive({ site_name: '', timezone: 'Asia/Shanghai' })
const baseline = ref<Preferences | null>(null)
const dirty = computed(() => !!baseline.value && (form.site_name !== baseline.value.site_name || form.timezone !== baseline.value.timezone))
const loading = ref(false), saving = ref(false), remoteChanged = ref(false), error = ref('')
protectPage(() => saving.value)
let loadVersion = 0
async function load(preserveDraft = false) {
  if (saving.value) return
  const mine = ++loadVersion
  if (!preserveDraft || !baseline.value) loading.value = true
  try {
    const data = await api<Preferences>('/system/preferences')
    if (mine !== loadVersion || saving.value) return
    // 全局状态通知不代表基础设置发生变化，也不能覆盖正在编辑的草稿。
    if (preserveDraft && dirty.value) {
      remoteChanged.value = data.revision !== baseline.value?.revision
      return
    }
    Object.assign(form, { site_name: data.site_name, timezone: data.timezone }); baseline.value = data
    remoteChanged.value = false; error.value = ''; emit('saved', data)
  } catch (cause) { if (mine === loadVersion) error.value = errorText(cause) }
  finally { if (mine === loadVersion) loading.value = false }
}
async function save() {
  if (!baseline.value || saving.value) return
  loadVersion++; loading.value = false
  saving.value = true; error.value = ''
  try {
    const data = await api<Preferences>('/system/preferences', { method: 'PUT', body: JSON.stringify({ ...form, revision: baseline.value.revision }) })
    Object.assign(form, { site_name: data.site_name, timezone: data.timezone }); baseline.value = data
    remoteChanged.value = false; emit('saved', data); ElMessage.success('基础设置已保存')
  } catch (cause) { error.value = errorText(cause) }
  finally { saving.value = false }
}
watch(() => props.refreshKey, () => { void load(true) })
onMounted(() => { void load() })
onScopeDispose(() => { loadVersion++ })
</script>

<template>
  <section class="panel compact-form settings-main" v-loading="loading">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="form-alert" />
    <el-alert v-if="remoteChanged" title="有新的状态通知，当前输入已保留。" type="warning" :closable="false" class="form-alert" />
    <el-form :model="form" label-position="top" @submit.prevent="save">
      <el-form-item label="管理台名称"><el-input v-model="form.site_name" maxlength="40" show-word-limit :disabled="saving || !baseline" /></el-form-item>
      <el-form-item label="显示时区"><el-select v-model="form.timezone" filterable allow-create default-first-option :disabled="saving || !baseline"><el-option label="中国标准时间 · Asia/Shanghai" value="Asia/Shanghai" /><el-option label="协调世界时 · UTC" value="UTC" /><el-option label="日本标准时间 · Asia/Tokyo" value="Asia/Tokyo" /><el-option label="美国东部时间 · America/New_York" value="America/New_York" /></el-select></el-form-item>
      <div class="form-actions settings-form-actions"><span v-if="dirty" class="muted">有未保存的修改</span><el-button :disabled="saving || loading" @click="load()">重新载入</el-button><el-button type="primary" native-type="submit" :loading="saving" :disabled="!dirty || loading">保存设置</el-button></div>
    </el-form>
  </section>
</template>
