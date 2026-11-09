<script lang="ts" setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { download, ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { addDays, deltaText, fmt, money, pct } from '@/utils/format'

defineOptions({ name: 'EceDashboard' })

const router = useRouter()
const userStore = useUserStore()

const filters = ref({ start: '', end: '', product: '', country: '' })
const dash = ref<any>(null)
const loading = ref(false)
const metric = ref<'quantity' | 'revenue' | 'orders'>('quantity')
const chartEl = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

const countries = computed(() => userStore.options.countries || [])
const products = computed(() => userStore.options.products || [])
const metricLabel = computed(
  () => ({ quantity: '销量 / 件', revenue: '销售额 / £', orders: '订单数' })[metric.value],
)

const kpis = computed(() => {
  const t = dash.value?.totals || {}
  const c = dash.value?.changes || {}
  return [
    { label: '销量 / 件', value: fmt(t.quantity), foot: deltaText(c.quantity) },
    { label: '销售额 / GBP', value: money(t.revenue), foot: deltaText(c.revenue) },
    { label: '订单数', value: fmt(t.orders), foot: deltaText(c.orders) },
    {
      label: '平均订单额 / GBP',
      value: money(t.average_order_value),
      foot: `覆盖 ${fmt(t.products)} 个商品 · ${fmt(t.lines)} 条正向交易行`,
    },
  ]
})

function qs(params: Record<string, any>) {
  return new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined) as string[][],
  ).toString()
}

function applyDefaults() {
  const m = userStore.manifest || {}
  const end = m.date_max || ''
  const start = end ? addDays(end, -29) : ''
  filters.value.start = start && m.date_min && start < m.date_min ? m.date_min : start
  filters.value.end = end
}

async function load() {
  if (!filters.value.start || !filters.value.end) {
    ElMessage.warning('请选择日期范围')
    return
  }
  loading.value = true
  try {
    dash.value = await ece.dashboard(filters.value)
    await nextTick()
    drawChart()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`看板读取失败：${error.message}`)
  } finally {
    loading.value = false
  }
}

function drawChart() {
  if (!chartEl.value || !dash.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const trend = dash.value.trend || []
  chart.setOption(
    {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          const item = params?.[0]
          if (!item) return ''
          const row = trend[item.dataIndex]
          const lines = [`${row.date}`]
          lines.push(`${metricLabel.value}：${fmt(row[metric.value], metric.value === 'revenue' ? 2 : 0)}`)
          if (row.observed === false) lines.push('（无符合口径的记录，按 0 显示）')
          return lines.join('<br/>')
        },
      },
      grid: { left: 52, right: 20, top: 24, bottom: 34 },
      xAxis: {
        type: 'category',
        data: trend.map((r: any) => String(r.date).slice(5)),
        boundaryGap: false,
        axisLabel: { color: '#93a39c', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#edf1e8', type: 'dashed' } },
        axisLabel: { color: '#93a39c', fontSize: 10 },
      },
      series: [
        {
          name: metricLabel.value,
          type: 'line',
          smooth: true,
          showSymbol: trend.length <= 14,
          data: trend.map((r: any) => (r[metric.value] === null ? null : r[metric.value])),
          lineStyle: { color: '#167967', width: 2.4 },
          itemStyle: { color: '#167967' },
          areaStyle: { color: 'rgba(22,121,103,0.10)' },
        },
      ],
    },
    true,
  )
}

function resize() {
  chart?.resize()
}

async function quickRange(days: number) {
  const m = userStore.manifest || {}
  const end = m.date_max || filters.value.end
  const startRaw = end ? addDays(end, -days + 1) : ''
  filters.value.end = end
  filters.value.start = startRaw && m.date_min && startRaw < m.date_min ? m.date_min : startRaw
  await load()
}

function applyFilters() {
  load()
}

function resetFilters() {
  filters.value.product = ''
  filters.value.country = ''
  applyDefaults()
  load()
}

function goProduct(sku: string) {
  router.push({ path: '/orders', query: { product: sku } })
}

function exportTrend() {
  if (!filters.value.start || !filters.value.end) return
  download(`/api/export/trend?${qs(filters.value)}`, 'sales-trend.csv')
}

function countryPercent(quantity: number) {
  const max = Math.max(1, ...(dash.value?.countries || []).map((c: any) => Number(c.quantity) || 0))
  return Math.round((Number(quantity) / max) * 100)
}

const quality = computed(() => dash.value?.quality || {})

onMounted(async () => {
  if (!userStore.options.countries?.length) await userStore.loadOptions()
  applyDefaults()
  await load()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})

watch(metric, () => {
  nextTick(drawChart)
})
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <div class="ece-eyebrow">SALES OVERVIEW</div>
        <h1>看板</h1>
        <p>按当前数据批次汇总销量、交易额与商品贡献；金额为 GBP 交易金额，不代表利润。</p>
      </div>
      <div class="ece-actions">
        <el-button @click="exportTrend">↓ 导出日趋势 CSV</el-button>
        <el-button type="primary" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="ece-panel">
      <el-form class="ece-filter" label-position="top" @submit.prevent>
        <el-form-item label="开始日期">
          <el-date-picker v-model="filters.start" type="date" value-format="YYYY-MM-DD" style="width: 148px" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="filters.end" type="date" value-format="YYYY-MM-DD" style="width: 148px" />
        </el-form-item>
        <el-form-item label="商品编码">
          <el-select
            v-model="filters.product"
            filterable
            clearable
            placeholder="全部商品"
            style="width: 210px"
          >
            <el-option
              v-for="p in products"
              :key="p.stock_code"
              :label="`${p.stock_code} · ${p.name || ''}`"
              :value="p.stock_code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="国家">
          <el-select v-model="filters.country" clearable placeholder="全部国家" style="width: 160px">
            <el-option v-for="c in countries" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label=" ">
          <el-button type="primary" :loading="loading" @click="applyFilters">应用筛选</el-button>
        </el-form-item>
        <el-form-item label=" ">
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
      <div class="quick-row">
        <span class="ece-muted ece-small">
          {{ filters.start || '—' }} 至 {{ filters.end || '—' }} · {{ fmt(dash?.totals?.lines) }} 条正向交易明细
        </span>
        <span>
          <el-button size="small" text @click="quickRange(30)">最近30天</el-button>
          <el-button size="small" text @click="quickRange(90)">最近90天</el-button>
          <el-button size="small" text @click="quickRange(365)">最近365天</el-button>
        </span>
      </div>
    </div>

    <div v-if="dash?.warnings?.length" class="ece-warnings">
      <el-alert
        v-for="(w, i) in dash.warnings"
        :key="i"
        :title="w"
        type="warning"
        :closable="false"
        show-icon
      />
    </div>

    <div class="ece-kpi-grid">
      <div v-for="k in kpis" :key="k.label" class="ece-kpi">
        <div class="ece-kpi__label">{{ k.label }}</div>
        <div class="ece-kpi__value">{{ k.value }}</div>
        <div class="ece-kpi__foot">{{ k.foot }}</div>
      </div>
    </div>

    <div class="dashboard-charts">
      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>销售趋势</h2>
            <p>按日汇总 · 无记录日按 0 显示（obs=0），覆盖范围外为空</p>
          </div>
          <el-radio-group v-model="metric" size="small">
            <el-radio-button value="quantity">销量</el-radio-button>
            <el-radio-button value="revenue">销售额</el-radio-button>
            <el-radio-button value="orders">订单数</el-radio-button>
          </el-radio-group>
        </div>
        <div ref="chartEl" class="ece-chart" />
        <div class="ece-muted ece-small">当前指标：{{ metricLabel }}</div>
      </div>

      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>国家汇总</h2>
            <p>按记录销量排序 · 前 10</p>
          </div>
          <el-tag size="small" effect="plain">QTY</el-tag>
        </div>
        <div v-if="dash?.countries?.length" class="country-list">
          <div v-for="c in dash.countries" :key="c.country" class="country-item">
            <span class="country-name" :title="c.country">{{ c.country || '未记录' }}</span>
            <el-progress
              :percentage="countryPercent(c.quantity)"
              :show-text="false"
              :stroke-width="8"
              color="#167967"
            />
            <span class="ece-mono">{{ fmt(c.quantity) }}</span>
          </div>
        </div>
        <div v-else class="ece-empty">暂无国家分布数据</div>
      </div>
    </div>

    <div class="ece-panel">
      <div class="ece-panel-head">
        <div>
          <h2>商品排名</h2>
          <p>按记录销量排序，点击商品可查看其交易明细</p>
        </div>
        <el-tag size="small" effect="plain">TOP {{ dash?.ranking?.length || 0 }}</el-tag>
      </div>
      <el-table :data="dash?.ranking || []" v-loading="loading" size="small" @row-click="(row: any) => goProduct(row.stock_code)">
        <el-table-column label="排名" width="64">
          <template #default="{ $index }">
            <span :class="['rank', { top: $index < 3 }]">{{ String($index + 1).padStart(2, '0') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="商品编码 / 名称" min-width="280">
          <template #default="{ row }">
            <b>{{ row.name || '未记录商品名称' }}</b>
            <span class="ece-mono sku-line">{{ row.stock_code }}</span>
          </template>
        </el-table-column>
        <el-table-column label="销量 / 件" width="120" align="right">
          <template #default="{ row }">{{ fmt(row.quantity) }}</template>
        </el-table-column>
        <el-table-column label="交易额 / £" width="140" align="right">
          <template #default="{ row }">{{ fmt(row.revenue, 2) }}</template>
        </el-table-column>
        <el-table-column label="订单数" width="100" align="right">
          <template #default="{ row }">{{ fmt(row.orders) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div class="ece-cards-2">
      <div class="ece-panel">
        <div class="ece-panel-head"><h2>质量提示</h2></div>
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="保留行数">{{ fmt(quality.retained_rows) }}</el-descriptions-item>
          <el-descriptions-item label="取消行">{{ fmt(quality.cancelled_rows) }}</el-descriptions-item>
          <el-descriptions-item label="非正数量行">{{ fmt(quality.nonpositive_rows) }}</el-descriptions-item>
          <el-descriptions-item label="重复候选行">{{ fmt(quality.duplicate_candidates) }}</el-descriptions-item>
          <el-descriptions-item label="无效单价行">{{ fmt(quality.invalid_price_rows) }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <div class="ece-panel">
        <div class="ece-panel-head"><h2>口径与批次</h2></div>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="当前批次">{{ dash?.batch?.label || userStore.batch?.label || '—' }}</el-descriptions-item>
          <el-descriptions-item label="数据覆盖">
            {{ dash?.coverage ? `${dash.coverage.start} ~ ${dash.coverage.end}` : userStore.coverage }}
          </el-descriptions-item>
          <el-descriptions-item label="口径版本">{{ dash?.policy_version || '—' }}</el-descriptions-item>
          <el-descriptions-item label="金额口径">GBP 交易金额；金额 ≠ 利润；无单价行保留销量但不计金额</el-descriptions-item>
          <el-descriptions-item label="零记录日">无记录日按 0 补入连续序列，仅表示没有符合口径的交易</el-descriptions-item>
          <el-descriptions-item label="本页边界">不含成本、退款利润、真实库存与流量；预测/补货为实验与模拟</el-descriptions-item>
        </el-descriptions>
      </div>
    </div>

    <div class="foot-note">
      正向交易口径：数量大于 0、非取消，排除跨表副本；销售额不代表利润。所选范围有
      {{ fmt(quality.cancelled_rows) }} 条取消记录另列，不混入正向销售。
    </div>
  </div>
</template>

<style scoped>
.dashboard-charts {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(240px, 0.9fr);
  gap: 16px;
}
.quick-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.country-list { display: grid; gap: 10px; }
.country-item { display: grid; grid-template-columns: 116px 1fr 56px; gap: 10px; align-items: center; font-size: 12px; }
.country-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #4d625d; }
.country-item :deep(.el-progress) { width: 100%; }
.rank { color: #9aa79f; font-variant-numeric: tabular-nums; }
.rank.top { color: #167967; font-weight: 700; }
.sku-line { display: block; color: #98a69e; font-size: 10.5px; }
.foot-note { color: #8a9a92; font-size: 11px; line-height: 1.8; padding: 2px 2px 10px; }
@media (max-width: 1100px) {
  .dashboard-charts { grid-template-columns: minmax(0, 1fr); }
}
</style>
