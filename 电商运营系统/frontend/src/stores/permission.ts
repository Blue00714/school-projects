import { computed } from 'vue'
import { defineStore } from 'pinia'
import { useUserStore } from '@/stores/user'

export interface EceMenu {
  path: string
  title: string
  icon: string
  adminOnly?: boolean
}

/** 仅保留本系统七个业务入口；商城演示菜单已删除。 */
export const ECE_MENUS: EceMenu[] = [
  { path: '/dashboard', title: '看板', icon: 'dashboard' },
  { path: '/orders', title: '商品与订单', icon: 'order' },
  { path: '/imports', title: '数据导入', icon: 'table' },
  { path: '/forecast', title: '预测与回测', icon: 'total-week' },
  { path: '/inventory', title: '库存与补货', icon: 'product' },
  { path: '/ai', title: 'AI 助手', icon: 'example' },
  { path: '/admin', title: '系统管理', icon: 'ums-admin', adminOnly: true },
]

export const usePermissionStore = defineStore('permission', () => {
  const userStore = useUserStore()
  const menus = computed(() =>
    ECE_MENUS.filter(item => !item.adminOnly || userStore.user?.role === 'admin'),
  )
  return { menus }
})

export default usePermissionStore
