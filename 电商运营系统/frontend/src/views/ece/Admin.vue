<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { fmt } from '@/utils/format'

defineOptions({ name: 'EceAdmin' })

const userStore = useUserStore()

const users = ref<any[]>([])
const usersLoading = ref(false)
const settings = ref<any>(null)
const settingsError = ref('')
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ username: '', display_name: '', password: '', role: 'operator' })

async function loadUsers() {
  usersLoading.value = true
  try {
    const data = await ece.users()
    users.value = data.users || []
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`账号读取失败：${error.message}`)
  } finally {
    usersLoading.value = false
  }
}

async function loadSettings() {
  try {
    settings.value = await ece.settings()
    settingsError.value = ''
  } catch (error: any) {
    settingsError.value = error.message
  }
}

async function toggleActive(user: any) {
  const action = user.active ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(`确认${action}账号 ${user.username}？`, '账号状态', {
      type: 'warning',
      confirmButtonText: action,
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await ece.patchUser(user.id, { active: !user.active })
    ElMessage.success(`账号已${action}`)
    await loadUsers()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`更新账号失败：${error.message}`)
  }
}

async function changeRole(user: any) {
  try {
    await ece.patchUser(user.id, { role: user.role })
    ElMessage.success(`角色已更新为 ${user.role}`)
    await loadUsers()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`修改角色失败：${error.message}`)
    await loadUsers()
  }
}

async function resetPassword(user: any) {
  let value = ''
  try {
    const result = await ElMessageBox.prompt(`为 ${user.username} 设置新密码（至少 8 位）`, '重置密码', {
      inputType: 'password',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValidator: (input: string) => (input && input.length >= 8 ? true : '新密码至少 8 位'),
    })
    value = result.value
  } catch {
    return
  }
  try {
    await ece.patchUser(user.id, { password: value })
    ElMessage.success('密码已重置')
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`重置密码失败：${error.message}`)
  }
}

async function createUser() {
  if (!createForm.username.trim()) {
    ElMessage.warning('请输入用户名')
    return
  }
  if (createForm.password.length < 8) {
    ElMessage.warning('初始密码至少 8 位')
    return
  }
  creating.value = true
  try {
    await ece.createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      display_name: createForm.display_name.trim(),
      role: createForm.role,
    })
    ElMessage.success(`已创建账号：${createForm.username.trim()}`)
    createVisible.value = false
    createForm.username = ''
    createForm.display_name = ''
    createForm.password = ''
    createForm.role = 'operator'
    await loadUsers()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`创建账号失败：${error.message}`)
  } finally {
    creating.value = false
  }
}

function aiConfigText() {
  const ai = settings.value?.ai || {}
  return [
    `已配置：${ai.configured ? '是' : '否'}`,
    `provider：${ai.provider || '—'}`,
    `模型：${ai.model || '—'}`,
    `密钥存在：${ai.key_present ? '是（页面不回显）' : '否'}`,
    `超时：${ai.timeout_seconds ?? '—'}s`,
  ].join(' · ')
}

onMounted(async () => {
  if (!userStore.isAdmin) {
    ElMessage.warning('系统管理仅管理员可见')
    return
  }
  await Promise.all([loadUsers(), loadSettings()])
})
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <div class="ece-eyebrow">ADMINISTRATION</div>
        <h1>系统管理</h1>
        <p>账号管理与非敏感配置只读展示（管理员可见）。</p>
      </div>
      <div class="ece-actions">
        <el-button @click="loadUsers">刷新账号</el-button>
        <el-button type="primary" @click="createVisible = true">新建账号</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="本页仅管理员可见；后端仍会独立校验权限（不能只靠隐藏菜单）。AI 密钥不会出现在页面或接口中。"
      class="mb12"
    />

    <div class="ece-cards-2">
      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>系统设置（只读）</h2>
            <p>不含 AI 密钥</p>
          </div>
        </div>
        <el-descriptions v-if="settings" :column="1" size="small" border>
          <el-descriptions-item label="当前批次">
            {{ settings.batch?.label || '—' }}（{{ settings.batch?.id || '—' }}）
          </el-descriptions-item>
          <el-descriptions-item label="AI 配置">
            <span class="ece-small">{{ aiConfigText() }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="会话 TTL">
            {{ settings.session_ttl_hours ?? '—' }} 小时
          </el-descriptions-item>
        </el-descriptions>
        <div v-else class="ece-empty">
          {{ settingsError ? `只读设置不可用：${settingsError}` : '正在读取设置…' }}
        </div>
      </div>

      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>账号规则</h2>
            <p>与后端校验一致，页面只负责展示与提交</p>
          </div>
        </div>
        <ul class="rule-list">
          <li>密码至少 8 位；用户名重复返回 409。</li>
          <li>不能停用当前登录账号；不能移除最后一个启用状态的管理员。</li>
          <li>修改密码会撤销该用户的其他会话。</li>
          <li>角色：admin（管理员）/ operator（运营）。</li>
        </ul>
      </div>
    </div>

    <div class="ece-panel">
      <div class="ece-panel-head">
        <div>
          <h2>用户列表</h2>
          <p>停用/启用、修改角色与重置密码</p>
        </div>
        <el-tag size="small" effect="plain">{{ users.length }} 个账号</el-tag>
      </div>
      <el-table :data="users" v-loading="usersLoading" size="small">
        <el-table-column label="ID" prop="id" width="70" />
        <el-table-column label="用户名" prop="username" width="150" />
        <el-table-column label="显示名" prop="display_name" min-width="140" />
        <el-table-column label="角色" width="150">
          <template #default="{ row }">
            <el-select v-model="row.role" size="small" @change="changeRole(row)">
              <el-option label="admin" value="admin" />
              <el-option label="operator" value="operator" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="row.active ? 'success' : 'danger'" size="small">
              {{ row.active ? '启用' : '停用' }}
            </el-tag>
            <el-tag v-if="row.must_change_password" type="info" size="small" style="margin-left: 4px">需改密</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="220">
          <template #default="{ row }">
            <el-button size="small" @click="toggleActive(row)">{{ row.active ? '停用' : '启用' }}</el-button>
            <el-button size="small" text @click="resetPassword(row)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="createVisible" title="新建账号" width="460px">
      <el-form label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="显示名（可选）">
          <el-input v-model="createForm.display_name" placeholder="显示名称" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option label="operator（运营）" value="operator" />
            <el-option label="admin（管理员）" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始密码（≥8 位）">
          <el-input v-model="createForm.password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createUser">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mb12 { margin-bottom: 12px; }
.rule-list { margin: 0; padding-left: 18px; color: #4a5f58; font-size: 12px; line-height: 2; }
</style>
