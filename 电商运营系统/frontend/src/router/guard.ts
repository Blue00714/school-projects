import router from '@/router/index'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'
import { useUserStore } from '@/stores/user'

const whiteList = ['/login']

/**
 * 认证守卫：
 * - 首次进入先调用 /api/auth/me 恢复 Cookie 会话；
 * - 未登录访问业务页 → /login；
 * - 已登录访问 /login → /dashboard；
 * - 系统管理仅 admin 可进（后端仍然会独立校验权限）。
 */
router.beforeEach(async to => {
  NProgress.start()
  const userStore = useUserStore()
  if (!userStore.ready) {
    await userStore.fetchMe()
  }
  const logged = !!userStore.user

  if (!logged) {
    if (whiteList.includes(to.path)) return true
    NProgress.done()
    return { path: '/login', query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }

  if (to.path === '/login') {
    NProgress.done()
    return { path: '/dashboard' }
  }

  if (to.meta.requiresAdmin && userStore.user?.role !== 'admin') {
    NProgress.done()
    return { path: '/dashboard' }
  }

  if (!userStore.batch) {
    await userStore.loadOptions()
  }
  return true
})

router.afterEach(() => {
  NProgress.done()
})
