<script setup lang="ts">
import SplitPane from './SplitPane.vue'
import EditorWorkspace from './EditorWorkspace.vue'
import { computed, onMounted, ref, toRaw, useId, watch } from 'vue'
import { api, errorText } from '../api'
import { newModel, newOptions } from '../model-types'
import type { ConnectionDraft, Protocol, SavedConnection } from '../model-types'
import ModelEntryEditor from './ModelEntryEditor.vue'
import NavIcon from './NavIcon.vue'
import type { PresetCatalog } from '../preset-types'
const presetCatalog = ref<PresetCatalog | null>(null)

const props = defineProps<{ connection: SavedConnection | null; protocols: Record<string, string> }>()
const emit = defineEmits<{ saved: []; cancel: [] }>()
const form = ref<ConnectionDraft>(props.connection ? {
  name: props.connection.name, protocol: props.connection.protocol, provider_kind: props.connection.provider_kind,
  endpoint_url: props.connection.endpoint_base_url ?? props.connection.endpoint_url,
  endpoint_mode: props.connection.endpoint_base_url !== null ? 'base' : props.connection.endpoint_mode,
  enabled: props.connection.enabled, timeout_seconds: props.connection.timeout_seconds,
  connect_timeout_seconds: props.connection.connect_timeout_seconds, max_attempts: props.connection.max_attempts,
  options: structuredClone(toRaw(props.connection.options)), models: structuredClone(toRaw(props.connection.models)),
  revision: props.connection.revision, key_action: 'keep', api_key: null,
} : { name: '', protocol: 'chat_completions', provider_kind: 'compatible', endpoint_url: '', endpoint_mode: 'base', enabled: true,
  timeout_seconds: 180, connect_timeout_seconds: 30, max_attempts: 3, options: newOptions(), models: [newModel()],
  revision: 0, key_action: 'keep', api_key: null })
const open = defineModel<boolean>('open', { required: true })
const selectedModel = ref(0), profileBusy = ref(false)
const section = ref('connection')
const sectionId = useId()
const chapters = computed(() => [
  { id: 'connection', label: '连接', icon: 'link' },
  { id: 'models', label: '模型能力', icon: 'settings' },
  ...(!form.value.protocol.startsWith('embedding_') ? [{ id: 'profile', label: '提示词与预设', icon: 'document' }] : []),
  { id: 'advanced', label: '高级选项', icon: 'integrations' },
])
function removeModel(index: number) { form.value.models.splice(index, 1); selectedModel.value = Math.min(selectedModel.value, form.value.models.length - 1) }
const saving = ref(false), error = ref('')
const placeholder = computed(() => {
  if (form.value.endpoint_mode === 'full') return '完整请求地址，仅用于自定义路由'
  if (form.value.protocol === 'embedding_ollama') return 'http://127.0.0.1:11434'
  if (['gemini', 'embedding_gemini'].includes(form.value.protocol)) return 'https://generativelanguage.googleapis.com'
  if (form.value.protocol === 'anthropic_messages') return 'https://api.anthropic.com'
  return form.value.provider_kind === 'openai' ? 'https://api.openai.com' : 'https://api.deepseek.com'
})
const endpointHint = computed(() => {
  const protocol = form.value.protocol
  if (form.value.endpoint_mode === 'full') return '按原地址请求，不自动补全路径。'
  if (protocol === 'embedding_ollama') return '填写服务基础地址，系统补全 /api/embed。'
  if (protocol === 'gemini' || protocol === 'embedding_gemini') return '例如 https://generativelanguage.googleapis.com，默认使用 /v1beta；也可保留服务商指定的版本前缀。'
  if (protocol === 'anthropic_messages') return '例如 https://api.anthropic.com，系统补全 /v1/messages；反代请保留服务商指定的版本前缀。'
  const operation = { responses: '/responses', chat_completions: '/chat/completions', embedding_openai: '/embeddings' }[protocol]
  return `例如 https://api.deepseek.com，自动补全 ${operation}；OpenAI 官方根地址另补 /v1，反代请保留服务商指定的 /v1 等前缀。`
})
watch(() => form.value.key_action, () => { form.value.api_key = null })
watch(() => form.value.protocol, (protocol: Protocol) => {
  form.value.options = newOptions()
  for (const model of form.value.models) { model.reasoning = null; model.reasoning_effort = null; model.tool_policy = null; if (protocol.startsWith('embedding_')) { model.image_input = null; model.tool_input = null; model.profile = null } }
})
async function save() {
  if (saving.value || profileBusy.value) return
  if (!form.value.name.trim() || !form.value.endpoint_url.trim()) { section.value = 'connection'; error.value = '请填写连接名称和接口地址。'; return }
  const incomplete = form.value.models.findIndex(model => !model.model.trim())
  if (incomplete !== -1) { section.value = 'models'; selectedModel.value = incomplete; error.value = '请填写模型 ID。'; return }
  saving.value = true; error.value = ''
  try {
    await api<SavedConnection>(`/models/connections${props.connection ? `/${props.connection.id}` : ''}`, {
      method: props.connection ? 'PUT' : 'POST', body: JSON.stringify(form.value),
    })
    form.value.api_key = null; emit('saved')
  } catch (cause) { error.value = errorText(cause) }
  finally { saving.value = false }
}
onMounted(async () => { try { presetCatalog.value = await api<PresetCatalog>('/models/presets') } catch (cause) { error.value = errorText(cause) } })
</script>

<template>
  <EditorWorkspace v-model="open" :title="connection ? '模型设置' : '新建连接'" :scope="connection?.name" :version="connection?.revision" :draft="form" :busy="saving || profileBusy" plain>
  <template #heading><el-switch v-model="form.enabled" active-text="启用连接" :disabled="saving || profileBusy" /><el-button type="primary" native-type="submit" :form="`${sectionId}-form`" :loading="saving" :disabled="profileBusy">保存连接</el-button></template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
  <el-form :id="`${sectionId}-form`" :model="form" label-position="top" :disabled="saving || profileBusy" @submit.prevent="save">
    <div class="settings-page-layout"><nav class="settings-rail" aria-label="连接设置"><button v-for="chapter in chapters" :key="chapter.id" type="button" :class="{ active: section === chapter.id }" :aria-current="section === chapter.id ? 'step' : undefined" :aria-controls="`${sectionId}-${['models', 'profile'].includes(chapter.id) ? 'model' : chapter.id}`" @click="section = chapter.id"><NavIcon :name="chapter.icon" /><span>{{ chapter.label }}</span></button></nav><div class="settings-page-body">
    <section v-show="section === 'connection'" :id="`${sectionId}-connection`" class="settings-chapter-card" aria-label="连接">
    <div class="form-two-columns"><el-form-item label="连接名称"><el-input v-model="form.name" maxlength="80" /></el-form-item><el-form-item label="协议"><el-select v-model="form.protocol"><el-option v-for="(label, value) in protocols" :key="value" :label="label" :value="value" /></el-select></el-form-item></div>
    <el-form-item :label="form.endpoint_mode === 'base' ? '接口地址' : '完整请求地址'"><el-input v-model="form.endpoint_url" :placeholder="placeholder" maxlength="2000" /><span class="field-hint">{{ endpointHint }}</span></el-form-item>
    <div class="credential-row"><el-form-item label="密钥处理"><el-select v-model="form.key_action"><el-option :label="connection?.key_configured ? '保留已保存密钥' : '不使用密钥'" value="keep" /><el-option label="填写新密钥" value="replace" /><el-option label="清除密钥" value="clear" /></el-select></el-form-item><el-form-item v-if="form.key_action === 'replace'" label="新密钥"><el-input v-model="form.api_key" type="password" show-password autocomplete="off" placeholder="只写入，不回显" /></el-form-item></div>
    </section>
    <section v-show="['models', 'profile'].includes(section)" :id="`${sectionId}-model`" :aria-label="section === 'profile' ? '提示词与预设' : '模型能力'">
    <div class="section-toolbar"><h3>{{ form.models[selectedModel]?.label || form.models[selectedModel]?.model || '当前模型' }}</h3><el-button :disabled="form.models.length >= 64" @click="form.models.push(newModel()); selectedModel = form.models.length - 1">添加模型</el-button></div>
    <ModelEntryEditor v-if="form.models.length === 1" v-model="form.models[0]!" :view="section === 'profile' ? 'profile' : 'basic'" :protocol="form.protocol" :provider-kind="form.provider_kind" :preset-catalog="presetCatalog" :removable="false" @open-profile="section = 'profile'" @busy-change="value => { profileBusy = value }" />
    <SplitPane v-else label="调整模型列表宽度" :selection="selectedModel" detail-label="当前模型"><template #list="{ open: openDetail }"><div class="editor-list"><button v-for="(entry, index) in form.models" :key="entry.id ?? `new-${index}`" type="button" :class="['editor-list-item', { active: selectedModel === index }]" @click="selectedModel = index; openDetail()"><strong>{{ entry.label || entry.model || '新模型' }}</strong><span>{{ entry.model || '尚未填写 ID' }}</span></button></div></template>
      <ModelEntryEditor v-if="form.models[selectedModel]" :key="selectedModel" :model-value="form.models[selectedModel]!" :view="section === 'profile' ? 'profile' : 'basic'" :protocol="form.protocol" :provider-kind="form.provider_kind" :preset-catalog="presetCatalog" :removable="form.models.length > 1" @open-profile="section = 'profile'" @update:model-value="value => { form.models[selectedModel] = value }" @remove="removeModel(selectedModel)" @busy-change="value => { profileBusy = value }" />
    </SplitPane>
    </section>
    <section v-show="section === 'advanced'" :id="`${sectionId}-advanced`" class="settings-chapter-card" aria-label="高级选项"><div class="form-two-columns">
      <el-form-item label="提供商适配"><el-select v-model="form.provider_kind"><el-option label="通用 / compatible" value="compatible" /><el-option v-for="kind in ['openai', 'deepseek', 'moonshot', 'anthropic', 'google']" :key="kind" :value="kind" :label="kind" /></el-select></el-form-item>
      <el-form-item label="地址方式"><el-select v-model="form.endpoint_mode"><el-option label="基础地址 · 自动补全" value="base" /><el-option label="完整地址 · 自定义路由" value="full" /></el-select></el-form-item>
      <el-form-item label="读取超时（秒）"><el-input-number v-model="form.timeout_seconds" :min="5" :max="600" controls-position="right" /></el-form-item>
      <el-form-item label="连接超时（秒）"><el-input-number v-model="form.connect_timeout_seconds" :min="1" :max="120" controls-position="right" /></el-form-item>
      <el-form-item label="网络最多尝试次数"><el-tooltip content="主叙事 HTTP 429 另有两次重试额度，每次等待60秒；整轮总时限仍生效。"><el-input-number v-model="form.max_attempts" :min="1" :max="5" controls-position="right" /></el-tooltip></el-form-item>
      <el-form-item v-if="form.protocol === 'chat_completions'" label="输出上限字段"><el-select v-model="form.options.output_limit_field"><el-option label="max_tokens" value="max_tokens" /><el-option label="max_completion_tokens" value="max_completion_tokens" /></el-select></el-form-item>
    </div><el-checkbox v-if="form.protocol === 'chat_completions'" v-model="form.options.stream_usage">请求流式用量</el-checkbox><el-checkbox v-if="form.protocol === 'responses' && (form.provider_kind !== 'deepseek' || form.options.include_encrypted_reasoning)" v-model="form.options.include_encrypted_reasoning">保留加密思考续接</el-checkbox><template v-if="form.protocol === 'gemini'"><el-form-item label="显式缓存资源地址"><el-input v-model="form.options.gemini_explicit_cache_url" placeholder="https://…/v1beta/cachedContents" /></el-form-item><el-checkbox v-model="form.options.gemini_explicit_cache_confirmed">已确认此模型与端点支持显式缓存</el-checkbox></template><el-checkbox v-if="form.protocol === 'anthropic_messages'" v-model="form.options.cache_control">启用显式缓存标记</el-checkbox></section>
    </div></div>
  </el-form>
  </EditorWorkspace>
</template>
