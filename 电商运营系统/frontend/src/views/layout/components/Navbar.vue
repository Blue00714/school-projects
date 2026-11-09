<script lang="ts" setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import Breadcrumb from '@/components/Breadcrumb/index.vue'
import Hamburger from '@/components/Hamburger/index.vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'

defineOptions({ name: 'Navbar' })

const appStore = useAppStore()
const userStore = useUserStore()
const router = useRouter()

const sidebar = computed(() => appStore.sidebar)
const batchLabel = computed(() => userStore.batch?.label || '批次未知')
const coverage = computed(() => userStore.coverage)
const aiConfigured = computed(() => Boolean(userStore.health?.ai_configured))

function toggleSideBar() {
  appStore.toggleSideBar()
}

async function handleLogout() {
  await userStore.logout()
  ElMessage.info('已退出登录')
  router.replace('/login')
}
</script>

<template>
  <div class="ece-navbar">
    <hamburger class="hamburger-container" :toggle-click="toggleSideBar" :is-active="sidebar.opened" />
    <breadcrumb />
    <div class="navbar-right">
      <el-tooltip :content="`当前数据批次：${batchLabel}`" placement="bottom">
        <span class="batch-chip">
          <i class="batch-dot" />
          <span class="batch-label">{{ batchLabel }}</span>
          <span class="batch-coverage">{{ coverage }}</span>
        </span>
      </el-tooltip>
      <el-tag :type="aiConfigured ? 'success' : 'warning'" effect="plain" size="small">
        {{ aiConfigured ? 'AI：已配置' : 'AI：未配置（本地规则）' }}
      </el-tag>
      <span class="user-chip">
        <b>{{ userStore.user?.display_name || userStore.user?.username || '—' }}</b>
        <el-tag size="small" :type="userStore.isAdmin ? 'primary' : 'info'" effect="plain">
          {{ userStore.user?.role || '—' }} · {{ userStore.roleLabel }}
        </el-tag>
      </span>
      <el-button size="small" @click="handleLogout">退出登录</el-button>
    </div>
  </div>
</template>

<style scoped>
.ece-navbar {
  height: 56px;
  background: #fff;
  border-bottom: 1px solid #e5e9e5;
  display: flex;
  align-items: center;
  padding: 0 18px;
  position: sticky;
  top: 0;
  z-index: 20;
  gap: 8px;
}
.hamburger-container { display: flex; align-items: center; padding: 0 6px; }
.navbar-right { margin-left: auto; display: flex; align-items: center; gap: 10px; min-width: 0; }
.batch-chip {
  display: inline-flex; align-items: center; gap: 7px; border: 1px solid #e5e9e5;
  border-radius: 999px; padding: 4px 12px; font-size: 12px; max-width: 420px;
}
.batch-dot { width: 7px; height: 7px; border-radius: 50%; background: #88ad6c; flex: 0 0 auto; }
.batch-label { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 210px; }
.batch-coverage { color: #8a9a92; font-size: 11px; white-space: nowrap; }
.user-chip { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; white-space: nowrap; }
.ece-navbar :deep(.app-breadcrumb) { min-width: 0; white-space: nowrap; overflow: hidden; flex-shrink: 0; }
@media (max-width: 1100px) {
  .batch-coverage { display: none; }
  .batch-label { max-width: 130px; }
}
@media (max-width: 760px) {
  .ece-navbar { padding: 0 10px; }
  .batch-chip { display: none; }
  .navbar-right { gap: 8px; flex-shrink: 0; }
  .user-chip :deep(.el-tag) { display: none; }
}
@media (max-width: 640px) {
  .navbar-right > :deep(.el-tag) { display: none; }
}
</style>
