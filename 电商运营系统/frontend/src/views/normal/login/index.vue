<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { ece } from '@/apis/ece'

defineOptions({ name: 'EceLogin' })

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const errorText = ref('')
const healthText = ref('正在检查本地服务…')

const form = reactive({ username: 'admin', password: '' })

const rules = reactive<FormRules<typeof form>>({
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
})

onMounted(async () => {
  try {
    const h = await ece.health()
    const bits = [
      h.service || 'ecommerce-ops-fastapi',
      `v${h.version || '—'}`,
      h.data_ready ? '数据就绪' : '数据未就绪',
      h.ai_configured ? 'AI：已配置' : 'AI：未配置（本地规则工具层）',
    ]
    if (h.batch?.label) bits.push(`批次：${h.batch.label}`)
    healthText.value = `服务正常 · ${bits.join(' · ')}`
  } catch {
    healthText.value = '服务状态未知：请确认本地 FastAPI 已启动'
  }
})

async function handleLogin() {
  if (!formRef.value) return
  await formRef.value.validate(async valid => {
    if (!valid) return
    loading.value = true
    errorText.value = ''
    try {
      await userStore.login(form.username.trim(), form.password)
      ElMessage.success('登录成功')
      const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
      router.replace(redirect.startsWith('/') ? redirect : '/dashboard')
    } catch (error: any) {
      errorText.value = error?.message || '登录失败'
    } finally {
      loading.value = false
    }
  })
}
</script>

<template>
  <div class="ece-login">
    <div class="ece-login__card">
      <div class="ece-login__brand">
        <span class="ece-login__mark">E</span>
        <div>
          <strong>智能电商运营管理系统</strong>
          <span class="ece-login__sub">OPERATIONS WORKSPACE · VUE3 + ELEMENT PLUS</span>
        </div>
      </div>
      <el-tag class="ece-login__ribbon" type="warning" effect="plain" size="small">课程原型 · 非生产环境</el-tag>
      <h1 class="ece-login__title">登录工作台</h1>
      <p class="ece-login__hint">使用本机账号登录；账号信息保存在 config/local/登录配置.ini 中。</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item label="账号" prop="username">
          <el-input v-model="form.username" autocomplete="username" placeholder="例如 admin" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="登录密码"
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-alert v-if="errorText" :title="errorText" type="error" :closable="false" show-icon class="mb12" />
        <el-button type="primary" class="w-full" :loading="loading" @click="handleLogin">登录</el-button>
      </el-form>
      <el-divider />
      <p class="ece-login__health">{{ healthText }}</p>
      <ul class="ece-login__notes">
        <li>销售额为 GBP 交易金额，不等于利润；无记录日按 0 补。</li>
        <li>库存为模拟数据，预测为固定模型实验，不代表真实经营收益。</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.ece-login {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  overflow: auto;
  background:
    radial-gradient(1100px 520px at 12% -8%, #2e4b45 0%, #17292f 55%, #101f24 100%);
}
.ece-login__card {
  width: min(440px, 100%);
  background: #fff;
  border-radius: 16px;
  padding: 32px 34px 24px;
  box-shadow: 0 30px 70px rgba(0, 0, 0, 0.35);
}
.ece-login__brand { display: flex; gap: 12px; align-items: center; }
.ece-login__mark {
  display: grid; place-items: center; width: 36px; height: 40px; border: 1px solid #91ae9b;
  border-radius: 9px; color: #c2dca7; font-size: 23px; background: #20342f;
}
.ece-login__brand strong { display: block; font-size: 17px; color: #172c32; }
.ece-login__sub { display: block; font-size: 8px; letter-spacing: 1.4px; color: #8aa39f; margin-top: 4px; }
.ece-login__ribbon { margin: 16px 0 12px; }
.ece-login__title { margin: 0 0 6px; font-size: 24px; color: #172c32; }
.ece-login__hint { margin: 0 0 18px; color: #758487; font-size: 12px; line-height: 1.7; }
.ece-login__health { margin: 0 0 10px; color: #758487; font-size: 11px; line-height: 1.7; }
.ece-login__notes { margin: 0; padding-left: 18px; color: #758487; font-size: 11px; line-height: 1.9; }
.w-full { width: 100%; }
.mb12 { margin-bottom: 12px; }
</style>
