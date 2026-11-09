<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import ScrollBar from '@/components/ScrollBar/index.vue'
import { useAppStore } from '@/stores/app'
import { usePermissionStore } from '@/stores/permission'

defineOptions({ name: 'Sidebar' })

const appStore = useAppStore()
const permissionStore = usePermissionStore()
const route = useRoute()

const isCollapse = computed(() => !appStore.sidebar.opened)
const menus = computed(() => permissionStore.menus)
</script>

<template>
  <div class="ece-sidebar">
    <div class="brand">
      <span class="brand-mark">E</span>
      <span class="brand-text">
        电商运营
        <small>OPERATIONS WORKSPACE</small>
      </span>
    </div>
    <div class="nav-label">业务工作台</div>
    <el-scrollbar>
      <el-menu
        mode="vertical"
        :default-active="route.path"
        :collapse="isCollapse"
        background-color="#17292f"
        text-color="#9fb5b0"
        active-text-color="#e9f5df"
        class="ece-menu"
      >
        <router-link v-for="item in menus" :key="item.path" :to="item.path">
          <el-menu-item :index="item.path">
            <svg-icon :icon-class="item.icon" class="menu-icon" />
            <template #title>{{ item.title }}</template>
          </el-menu-item>
        </router-link>
      </el-menu>
    </el-scrollbar>
    <div class="sidebar-bottom">
      <div class="source-label"><i /> 本地离线原型</div>
      <strong>Online Retail II</strong>
      <p>单店历史记录 · 销量/交易额<br />无真实库存与成本数据</p>
      <span class="local-label">LOCAL · NO CDN</span>
    </div>
  </div>
</template>

<style scoped>
.ece-sidebar {
  height: 100%;
  background: #17292f;
  padding: 18px 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.brand { display: flex; gap: 11px; align-items: center; color: #fff; font-size: 17px; font-weight: 650; padding: 0 8px; }
.brand-mark {
  display: grid; place-items: center; width: 34px; height: 37px; border: 1px solid #91ae9b;
  border-radius: 8px; color: #c2dca7; font-size: 22px; flex: 0 0 auto;
}
.brand-text { line-height: 1.2; }
.brand-text small { display: block; font-size: 8px; letter-spacing: 1.3px; color: #8aa39f; margin-top: 5px; }
.nav-label { font-size: 10px; color: #78928f; letter-spacing: 2px; margin: 38px 14px 10px; }
.ece-menu { border-right: none; }
.ece-menu :deep(.el-menu-item) { height: 46px; border-radius: 8px; margin: 3px 4px; }
.ece-menu :deep(.el-menu-item.is-active) { background: #314b43; }
.ece-menu :deep(.el-menu-item:hover) { background: #223c40; }
.menu-icon { width: 17px; height: 17px; margin-right: 10px; }
.sidebar-bottom { margin-top: auto; border-top: 1px solid #33454a; padding: 16px 12px 0; }
.source-label { font-size: 11px; color: #b8c7bf; margin-bottom: 8px; }
.source-label i { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #a9c27b; margin-right: 8px; }
.sidebar-bottom strong { font-size: 14px; color: #e9eeea; display: block; }
.sidebar-bottom p { font-size: 11px; line-height: 1.9; color: #8fa7a0; margin: 4px 0 8px; }
.local-label { font-size: 9px; letter-spacing: 1.4px; color: #637f7a; }
</style>
