import { createRouter, createWebHashHistory } from 'vue-router'
import Layout from '@/views/layout/Layout.vue'
import type { RouteRecordExt } from '@/types/router'

/**
 * 路由：只保留本系统七个业务入口（看板 / 商品与订单 / 数据导入 / 预测与回测 /
 * 模拟库存 / AI 助手 / 系统管理）。商城演示模块与 mock 页面已全部删除。
 */
export const constantRouterMap: RouteRecordExt[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/normal/login/index.vue'),
    hidden: true,
  },
  {
    path: '',
    component: Layout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/ece/Dashboard.vue'),
        meta: { title: '看板', icon: 'dashboard' },
      },
      {
        path: 'orders',
        name: 'orders',
        component: () => import('@/views/ece/Orders.vue'),
        meta: { title: '商品与订单', icon: 'order' },
      },
      {
        path: 'imports',
        name: 'imports',
        component: () => import('@/views/ece/Imports.vue'),
        meta: { title: '数据导入', icon: 'table' },
      },
      {
        path: 'forecast',
        name: 'forecast',
        component: () => import('@/views/ece/Forecast.vue'),
        meta: { title: '预测与回测', icon: 'total-week' },
      },
      {
        path: 'inventory',
        name: 'inventory',
        component: () => import('@/views/ece/Inventory.vue'),
        meta: { title: '库存与补货', icon: 'product' },
      },
      {
        path: 'ai',
        name: 'ai',
        component: () => import('@/views/ece/Ai.vue'),
        meta: { title: 'AI 助手', icon: 'example' },
      },
      {
        path: 'admin',
        name: 'admin',
        component: () => import('@/views/ece/Admin.vue'),
        meta: { title: '系统管理', icon: 'ums-admin', requiresAdmin: true },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'notFound',
    component: () => import('@/views/normal/404/index.vue'),
    hidden: true,
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes: constantRouterMap,
  scrollBehavior: () => ({ top: 0 }),
})

export default router
