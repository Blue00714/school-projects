import { createApp } from 'vue'
import App from './App.vue'
import { createPinia } from 'pinia'
import router from './router'
import '@/styles/index.scss' // 模板全局样式
import '@/styles/ece.scss' // 本系统页面样式
import 'normalize.css/normalize.css'
import { setupSvgIcon } from './icons'
import 'virtual:svg-icons-register'
import '@/router/guard'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
setupSvgIcon(app)
app.mount('#app')
