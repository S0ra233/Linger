import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { startAppearance } from './appearance'

startAppearance()
createApp(App).use(ElementPlus, { locale: zhCn }).mount('#app')
