<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, type Component } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorText, setCsrf } from './api'
import type { AdminUser, AuthResponse, Preferences } from './api'
import NavIcon from './components/NavIcon.vue'
import AppearanceMenu from './components/AppearanceMenu.vue'
import { editorCount, mayLeave, selectedLifeLine } from './ui-state'
import LoginPage from './components/LoginPage.vue'
import AsyncPageLoading from './components/AsyncPageLoading.vue'
import AsyncPageError from './components/AsyncPageError.vue'
import OverviewPage from './pages/OverviewPage.vue'
const PreferencesPage = pageComponent(() => import('./pages/PreferencesPage.vue'))
const SecurityPage = pageComponent(() => import('./pages/SecurityPage.vue'))
const ConversationArchivesPage = pageComponent(() => import('./pages/ConversationArchivesPage.vue'))
const AuditPage = pageComponent(() => import('./pages/AuditPage.vue'))
const ModelsPage = pageComponent(() => import('./pages/ModelsPage.vue'))
const MediaPage = pageComponent(() => import('./pages/MediaPage.vue'))
const CharactersPage = pageComponent(() => import('./pages/CharactersPage.vue'))
const QQPage = pageComponent(() => import('./pages/QQPage.vue'))
const ContactsPage = pageComponent(() => import('./pages/ContactsPage.vue'))
const RuntimePage = pageComponent(() => import('./pages/RuntimePage.vue'))
const WorldbooksPage = pageComponent(() => import('./pages/WorldbooksPage.vue'))
const ContextPage = pageComponent(() => import('./pages/ContextPage.vue'))
const ContextStrategiesPage = pageComponent(() => import('./pages/ContextStrategiesPage.vue'))
const PresetsPage = pageComponent(() => import('./pages/PresetsPage.vue'))
const MemoriesPage = pageComponent(() => import('./pages/MemoriesPage.vue'))
const IndexesPage = pageComponent(() => import('./pages/IndexesPage.vue'))
const McpPage = pageComponent(() => import('./pages/McpPage.vue'))
const LifePage = pageComponent(() => import('./pages/LifePage.vue'))
const KnowledgePage = pageComponent(() => import('./pages/KnowledgePage.vue'))
const MaintenancePage = pageComponent(() => import('./pages/MaintenancePage.vue'))
const UsagePage = pageComponent(() => import('./pages/UsagePage.vue'))


function pageComponent(loader: () => Promise<{ default: Component }>) {
  return defineAsyncComponent({
    loader: async () => {
      const requested = window.location.hash || '#overview'
      const component = await loader()
      // 首次异步载入后再恢复滚动；迟到页面不能改变当前页面的位置。
      window.requestAnimationFrame(() => {
        if (approvedHash === requested && !settingsOpen.value && !editorCount.value) {
          window.scrollTo(0, scrollPositions.get(requested) ?? 0)
        }
      })
      return component
    },
    loadingComponent: AsyncPageLoading,
    errorComponent: AsyncPageError,
    delay: 160,
    timeout: 30000,
  })
}

const groups = [
  { id: 'overview', label: '概览', pages: [{ id: 'overview', label: '运行概览' }] },
  { id: 'contacts', label: '联系人', pages: [{ id: 'contacts', label: '联系人' }, { id: 'memories', label: '私有记忆' }, { id: 'strategies', label: '上下文策略' }] },
  { id: 'materials', label: '角色与资料', pages: [{ id: 'characters', label: '角色模板' }, { id: 'worldbooks', label: '世界书' }, { id: 'knowledge', label: '知识库' }, { id: 'indexes', label: '语义索引' }, { id: 'media', label: '表情包与图片' }] },
  { id: 'models', label: '模型', pages: [{ id: 'models', label: '模型连接' }, { id: 'presets', label: '提示词预设' }] },
  { id: 'runtime', label: '计划与记录', pages: [{ id: 'life', label: '计划与回收' }, { id: 'runtime', label: '模型运行' }, { id: 'usage', label: '用量与估算' }] },
  { id: 'integrations', label: '接入与维护', pages: [{ id: 'qq', label: 'QQ 接入' }, { id: 'mcp', label: 'MCP' }, { id: 'maintenance', label: '部署与维护' }, { id: 'audit', label: '操作记录' }] },
]
const settingsGroup = { id: 'settings', label: '设置', pages: [{ id: 'preferences', label: '基础设置' }, { id: 'security', label: '账户安全' }, { id: 'archives', label: '已归档对话' }] }
// 旧预览地址继续可用，主要入口统一在策略页的占用与效果。
const navigation = [...groups.flatMap(group => group.pages), ...settingsGroup.pages, { id: 'context', label: '上下文预览' }]
const page = ref('overview'), contentPage = ref('overview'), settingsPage = ref('preferences'), mobileNav = ref(false)
const activePage = computed(() => navigation.find(item => item.id === contentPage.value) ?? navigation[0]!)
const settingsOpen = computed(() => settingsGroup.pages.some(item => item.id === page.value))
const activeGroup = computed(() => groups.find(group => group.pages.some(item => item.id === contentPage.value) || (group.id === 'contacts' && contentPage.value === 'context')) ?? groups[0]!)
const scrollPositions = new Map<string, number>()
let approvedHash = '#overview', contentHash = '#overview', routeVersion = 0, previousHash = '#overview'
let settingsReturnFocus: HTMLElement | null = null
const settingsTrigger = ref<HTMLAnchorElement | null>(null)
const user = ref<AdminUser | null>(null)
const preferences = ref<Preferences | null>(null)
const initializing = ref(true)
const initialError = ref('')
const loggingOut = ref(false)
const refreshKey = ref(0)
const eventConnected = ref(false)
let eventSource: EventSource | null = null
let checkingSession = false
let refreshTimer: number | undefined

async function readHash() {
  const mine = ++routeVersion, requested = window.location.hash || '#overview'
  const [id, query = ''] = requested.slice(1).split('?')
  const next = navigation.some(item => item.id === id) ? id! : 'overview'
  if (requested !== approvedHash && !await mayLeave()) {
    if (mine === routeVersion) window.history.replaceState(null, '', approvedHash)
    return
  }
  if (mine !== routeVersion) return
  const wasSettings = settingsOpen.value
  const nextSettings = settingsGroup.pages.some(item => item.id === next)
  const contentChanged = !nextSettings && requested !== contentHash
  if (!wasSettings) scrollPositions.set(contentHash, window.scrollY)
  // 设置只是覆盖当前页面；不卸载原页面、整页编辑或它们的草稿。
  if (nextSettings) {
    if (!wasSettings) {
      previousHash = contentHash
      settingsReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    }
    settingsPage.value = next
  } else {
    contentPage.value = next; contentHash = requested
  }
  const instance = new URLSearchParams(query).get('instance')
  if (instance) selectedLifeLine.value = instance
  page.value = next; approvedHash = requested; mobileNav.value = false
  await nextTick()
  if (mine === routeVersion && !nextSettings && (contentChanged || !wasSettings)) window.scrollTo(0, scrollPositions.get(requested) ?? 0)
}
function closeSettings() { window.location.hash = previousHash }
async function afterSettingsClosed() {
  await nextTick()
  if (settingsOpen.value || !user.value) return
  let target = settingsReturnFocus?.isConnected ? settingsReturnFocus : settingsTrigger.value
  if (target && getComputedStyle(target).visibility === 'hidden') target = document.querySelector<HTMLButtonElement>('.mobile-menu')
  target?.focus({ preventScroll: true }); settingsReturnFocus = null
}
function endEvents() { eventSource?.close(); eventSource = null; eventConnected.value = false; window.clearTimeout(refreshTimer); refreshTimer = undefined }
function forgetSession() { endEvents(); scrollPositions.clear(); user.value = null; preferences.value = null; selectedLifeLine.value = ''; setCsrf('') }
function onExpired() { if (user.value) ElMessage.warning('登录已失效，请重新登录。'); forgetSession() }
function onPreferences(value: Preferences) { preferences.value = value; document.title = value.site_name }

function startEvents() {
  endEvents()
  const source = new EventSource('/api/v1/events')
  eventSource = source
  source.onopen = () => { if (eventSource === source) { eventConnected.value = true; refreshKey.value++ } }
  source.addEventListener('expired', () => { if (eventSource === source) onExpired() })
  source.addEventListener('refresh', event => {
    if (eventSource !== source) return
    // 连续输入与图片状态合并一次通知，避免每个段都重读整页。
    if (refreshTimer === undefined) refreshTimer = window.setTimeout(() => { refreshKey.value += 1; refreshTimer = undefined }, 400)
    const change = JSON.parse((event as MessageEvent<string>).data) as { kind: string }
    if (change.kind === 'preferences') {
      void api<Preferences>('/system/preferences').then(onPreferences).catch(error => ElMessage.error(errorText(error)))
    }
  })
  source.onerror = () => {
    if (eventSource !== source) return
    eventConnected.value = false
    // 重连若得到 401，不会再收到 expired 事件；补读会话以退出旧界面。
    if (user.value && !checkingSession) {
      checkingSession = true
      void api<AuthResponse>('/auth/session').catch(() => {}).finally(() => { checkingSession = false })
    }
  }
}
async function authenticated(value: AuthResponse) {
  user.value = value.user
  setCsrf(value.csrf_token)
  startEvents()
  try { onPreferences(await api<Preferences>('/system/preferences')) }
  catch (error) { ElMessage.error(errorText(error)) }
}
async function restore() {
  initializing.value = true
  initialError.value = ''
  try { await authenticated(await api<AuthResponse>('/auth/session')) }
  catch (error) {
    if (!(error instanceof Error && 'status' in error && error.status === 401)) initialError.value = errorText(error)
  } finally { initializing.value = false }
}
async function logout() {
  if (!await mayLeave()) return
  loggingOut.value = true
  try { await api<void>('/auth/logout', { method: 'POST' }); forgetSession() }
  catch (error) { ElMessage.error(errorText(error)) }
  finally { loggingOut.value = false }
}
onMounted(() => { readHash(); window.addEventListener('hashchange', readHash); window.addEventListener('rp-auth-expired', onExpired); void restore() })
onUnmounted(() => { endEvents(); window.removeEventListener('hashchange', readHash); window.removeEventListener('rp-auth-expired', onExpired) })
</script>

<template>
  <div v-if="initializing" class="loading-screen"><span class="brand-symbol">rp<span>·</span></span><p>正在读取管理会话…</p></div>
  <div v-else-if="initialError" class="loading-screen"><h2>暂时无法连接管理服务</h2><p>{{ initialError }}</p><el-button @click="restore">重新连接</el-button></div>
  <LoginPage v-else-if="!user" @authenticated="authenticated" />
  <div v-else class="app-layout" @keydown.esc="mobileNav = false">
    <button v-if="mobileNav" class="nav-backdrop" aria-label="关闭导航" @click="mobileNav = false"></button>
    <aside :class="['sidebar', { 'mobile-open': mobileNav }]" :inert="settingsOpen || undefined">
      <div class="sidebar-brand-slot" aria-hidden="true"></div>
      <nav aria-label="管理导航" class="primary-navigation">
        <div v-for="group in groups" :key="group.id" class="navigation-group">
          <a :href="`#${group.pages[0]!.id}`" :class="['navigation-parent', { active: activeGroup.id === group.id }]" :aria-current="group.pages.length === 1 && activeGroup.id === group.id ? 'page' : undefined"><NavIcon :name="group.id" /><span>{{ group.label }}</span><NavIcon v-if="group.pages.length > 1" :class="['navigation-chevron', { expanded: activeGroup.id === group.id }]" name="chevron" /></a>
          <div v-if="activeGroup.id === group.id && group.pages.length > 1" class="navigation-children">
            <a v-for="item in group.pages" :key="item.id" :href="`#${item.id}`" :class="['navigation-child', { active: contentPage === item.id || item.id === 'strategies' && contentPage === 'context' }]" :aria-current="contentPage === item.id ? 'page' : undefined">{{ item.label }}</a>
          </div>
        </div>
      </nav>
      <div class="sidebar-bottom">
        <AppearanceMenu />
        <nav aria-label="管理设置" class="secondary-navigation"><a ref="settingsTrigger" href="#preferences" :class="{ active: settingsOpen }" aria-haspopup="dialog" :aria-expanded="settingsOpen"><NavIcon name="settings" />设置</a></nav>
        <div class="sidebar-user"><div class="avatar">{{ user.username.slice(0, 1).toUpperCase() }}</div><strong>{{ user.username }}</strong><el-button link :loading="loggingOut" @click="logout">退出</el-button></div>
      </div>
    </aside>
    <div class="workspace" :inert="settingsOpen || undefined">
      <header class="topbar"><div class="breadcrumb"><el-button class="mobile-menu" aria-label="打开导航" :aria-expanded="mobileNav" @click="mobileNav = !mobileNav">☰</el-button><span>{{ activeGroup.label }}</span><template v-if="activePage.label !== activeGroup.label"><span aria-hidden="true">/</span><span class="breadcrumb-current">{{ activePage.label }}</span></template></div><div class="connection-label"><span :class="['quiet-dot', { offline: !eventConnected }]"></span>{{ eventConnected ? '更新已连接' : '更新重连中' }}<el-button v-if="contentPage !== 'overview'" link @click="refreshKey++">刷新</el-button><el-button class="mobile-logout" link :loading="loggingOut" @click="logout">退出</el-button></div></header>
      <main class="main-content">
        <div id="editor-host"></div>
        <div v-show="!editorCount" class="page-body"><div v-if="contentPage !== 'overview'" class="page-heading"><h1>{{ activePage.label }}</h1></div>
        <OverviewPage v-if="contentPage === 'overview'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <ModelsPage v-else-if="contentPage === 'models'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <MediaPage v-else-if="contentPage === 'media'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <PresetsPage v-else-if="contentPage === 'presets'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <CharactersPage v-else-if="contentPage === 'characters'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <WorldbooksPage v-else-if="contentPage === 'worldbooks'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <MemoriesPage v-else-if="contentPage === 'memories'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <KnowledgePage v-else-if="contentPage === 'knowledge'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <IndexesPage v-else-if="contentPage === 'indexes'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <ContextStrategiesPage v-else-if="contentPage === 'strategies'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <ContextPage v-else-if="contentPage === 'context'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <ContactsPage v-else-if="contentPage === 'contacts'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <RuntimePage v-else-if="contentPage === 'runtime'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <LifePage v-else-if="contentPage === 'life'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <AuditPage v-else-if="contentPage === 'audit'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <MaintenancePage v-else-if="contentPage === 'maintenance'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <UsagePage v-else-if="contentPage === 'usage'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <QQPage v-else-if="contentPage === 'qq'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        <McpPage v-else-if="contentPage === 'mcp'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
        </div>
        <el-dialog :model-value="settingsOpen" title="设置" width="min(840px, calc(100vw - var(--rp-settings-gutter, 64px)))" class="settings-dialog" modal-class="settings-overlay" transition="settings-surface" align-center append-to-body destroy-on-close :close-on-click-modal="false" :before-close="() => closeSettings()" @closed="afterSettingsClosed">
          <template #header="{ titleId }"><div class="settings-window-heading"><span class="settings-window-symbol"><NavIcon name="settings" /></span><h2 :id="titleId">设置</h2></div></template>
          <div class="settings-window"><nav aria-label="设置分类"><a v-for="item in settingsGroup.pages" :key="item.id" :href="`#${item.id}`" :class="{ active: settingsPage === item.id }" :aria-current="settingsPage === item.id ? 'page' : undefined"><NavIcon :name="item.id === 'security' ? 'lock' : 'settings'" />{{ item.label }}</a><a href="#audit"><NavIcon name="document" />操作记录 ↗</a></nav><div class="settings-window-content">
            <PreferencesPage v-if="settingsPage === 'preferences'" :refresh-key="refreshKey" @saved="onPreferences" />
            <SecurityPage v-else-if="settingsPage === 'security'" :refresh-key="refreshKey" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" @password-changed="forgetSession" />
            <ConversationArchivesPage v-else-if="settingsPage === 'archives'" :timezone="preferences?.timezone ?? 'Asia/Shanghai'" />
          </div></div>
        </el-dialog>
      </main>
    </div>
  </div>
</template>
