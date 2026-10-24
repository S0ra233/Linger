import { onScopeDispose, ref, type Ref } from 'vue'

// 只保存界面选择；所有资料授权仍由服务器按生活线核对。
export const selectedLifeLine = ref('')
export const editorCount = ref(0)
const leaveGuards = new Set<() => Promise<boolean>>()

export function registerLeaveGuard(guard: () => Promise<boolean>) {
  leaveGuards.add(guard)
  onScopeDispose(() => leaveGuards.delete(guard))
}
export async function mayLeave() {
  for (const guard of leaveGuards) if (!await guard()) return false
  return true
}
// 普通草稿可直接离开，只阻止正在提交的页面被关闭。
export function useEditorClose(open: Ref<boolean>, busy: () => boolean) {
  async function allowLeave() { return !open.value || !busy() }
  async function close() { if (await allowLeave()) open.value = false }
  function beforeUnload(event: BeforeUnloadEvent) {
    if (open.value && busy()) { event.preventDefault(); event.returnValue = '' }
  }
  window.addEventListener('beforeunload', beforeUnload)
  onScopeDispose(() => window.removeEventListener('beforeunload', beforeUnload))
  registerLeaveGuard(allowLeave)
  return { close, allowLeave }
}

export function protectPage(busy: () => boolean) {
  registerLeaveGuard(async () => !busy())
  const unload = (event: BeforeUnloadEvent) => { if (busy()) { event.preventDefault(); event.returnValue = '' } }
  window.addEventListener('beforeunload', unload)
  onScopeDispose(() => window.removeEventListener('beforeunload', unload))
}
