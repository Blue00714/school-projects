<script lang="ts" setup>
import { useRouter } from 'vue-router'

defineOptions({ name: 'NotFound' })

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <el-result icon="warning" title="404" sub-title="页面不存在或已被移除">
      <template #extra>
        <el-button type="primary" @click="router.replace('/dashboard')">返回看板</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.not-found {
  min-height: 60vh;
  display: grid;
  place-items: center;
  padding: 40px 20px;
}
</style>
