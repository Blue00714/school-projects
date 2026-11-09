import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ece, type BatchInfo, type EceOptions, type EceUser } from '@/apis/ece'

/**
 * 会话与批次状态。
 * 登录态由后端 HttpOnly Cookie 维护，本 store 只缓存用户信息与当前批次元数据；
 * 不做 localStorage token 持久化（避免与真实会话不一致）。
 */
export const useUserStore = defineStore('user', () => {
  const user = ref<EceUser | null>(null)
  const ready = ref(false)
  const batch = ref<BatchInfo | null>(null)
  const manifest = ref<Record<string, any>>({})
  const options = ref<EceOptions>({ manifest: {}, countries: [], products: [], methods: {} })
  const health = ref<any>(null)

  const roleLabel = computed(() =>
    user.value?.role === 'admin' ? '管理员' : user.value?.role === 'operator' ? '运营' : '—',
  )
  const coverage = computed(() => {
    const m = manifest.value || {}
    return m.date_min && m.date_max ? `${m.date_min} ~ ${m.date_max}` : '—'
  })
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function fetchMe() {
    try {
      const data = await ece.me()
      user.value = data.user || null
      if (user.value) await loadOptions()
    } catch {
      user.value = null
    } finally {
      ready.value = true
    }
    return user.value
  }

  async function login(username: string, password: string) {
    const data = await ece.login(username, password)
    user.value = data.user
    ready.value = true
    await loadOptions()
    return user.value
  }

  async function logout() {
    try {
      await ece.logout()
    } catch {
      /* 退出失败也清理本地状态 */
    }
    user.value = null
    batch.value = null
    manifest.value = {}
    ready.value = true
  }

  async function loadOptions() {
    try {
      const data = await ece.options()
      options.value = data
      batch.value = data.batch || batch.value
      manifest.value = data.manifest || {}
    } catch {
      /* 选项读取失败时保留旧值；页面会显示空态 */
    }
    try {
      health.value = await ece.health()
    } catch {
      /* 健康检查失败不阻塞页面 */
    }
  }

  return {
    user,
    ready,
    batch,
    manifest,
    options,
    health,
    roleLabel,
    coverage,
    isAdmin,
    fetchMe,
    login,
    logout,
    loadOptions,
  }
})
