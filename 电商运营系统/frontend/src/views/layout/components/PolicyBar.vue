<script lang="ts" setup>
import { computed } from 'vue'
import { useUserStore } from '@/stores/user'

defineOptions({ name: 'PolicyBar' })

const userStore = useUserStore()
const batchLabel = computed(() => userStore.batch?.label || '批次未知')
const coverage = computed(() => userStore.coverage)
</script>

<template>
  <div class="policy-strip">
    <span><b>口径</b>：交易销售额为 GBP 交易额，<em>不等于利润</em>；无记录日按 0 补，不代表市场无需求。</span>
    <span><b>范围</b>：{{ batchLabel }} · {{ coverage }}</span>
    <span><b>边界</b>：库存为<em>模拟</em>；预测为固定模型实验，不构成经营收益结论。</span>
  </div>
</template>

<style scoped>
.policy-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 22px;
  padding: 7px 22px;
  font-size: 11px;
  color: #5d7176;
  background: #eef3ec;
  border-bottom: 1px solid #e5e9e5;
}
.policy-strip b { color: #415b60; font-weight: 600; }
.policy-strip em { font-style: normal; color: #9a5a1e; font-weight: 600; }
</style>
