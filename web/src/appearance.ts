import { reactive, watch } from 'vue'

type Theme = 'blue' | 'warm'
interface Appearance { theme: Theme; glass: boolean; reduceMotion: boolean }
const storageKey = 'rp.appearance.v1'
const defaults: Appearance = { theme: 'blue', glass: true, reduceMotion: false }

function readAppearance(): Appearance {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    if (!saved || typeof saved !== 'object') return { ...defaults }
    const value = saved as Partial<Appearance>
    return {
      theme: value.theme === 'warm' ? 'warm' : 'blue',
      glass: typeof value.glass === 'boolean' ? value.glass : defaults.glass,
      reduceMotion: typeof value.reduceMotion === 'boolean' ? value.reduceMotion : defaults.reduceMotion,
    }
  } catch { return { ...defaults } }
}

// 外观仅属于当前浏览器；不进入模型配置、请求或服务端资料。
export const appearance = reactive(readAppearance())
export function startAppearance() {
  return watch(appearance, value => {
    const root = document.documentElement
    root.dataset.theme = value.theme
    root.dataset.glass = String(value.glass)
    root.dataset.reduceMotion = String(value.reduceMotion)
    try { localStorage.setItem(storageKey, JSON.stringify(value)) } catch { /* 禁止本地存储时仍允许本次切换。 */ }
  }, { deep: true, immediate: true })
}
