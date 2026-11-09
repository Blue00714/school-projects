<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

defineOptions({ name: 'Breadcrumb' })

const route = useRoute()

const items = computed(() =>
  route.matched.filter(item => item.meta?.title).map(item => ({ path: item.path, title: item.meta.title as string })),
)
</script>

<template>
  <el-breadcrumb class="app-breadcrumb" separator="/">
    <el-breadcrumb-item v-for="item in items" :key="item.path">
      <span class="no-redirect">{{ item.title }}</span>
    </el-breadcrumb-item>
  </el-breadcrumb>
</template>

<style lang="scss" scoped>
.app-breadcrumb.el-breadcrumb {
  display: inline-block;
  font-size: 14px;
  line-height: 56px;
  margin-left: 12px;

  .no-redirect {
    color: #97a8be;
    cursor: text;
  }
}
</style>
