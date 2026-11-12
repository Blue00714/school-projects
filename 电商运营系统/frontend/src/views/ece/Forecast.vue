<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { download, ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { addDays, daysBetween, fmt, pct } from '@/utils/format'

defineOptions({ name: 'EceForecast' })

const userStore = useUserStore()

const origin = ref('')
const forecastRunning = ref(false)
const forecastTaskText = ref('尚未提交预测任务')
const forecastTaskType = ref<'info' | 'success' | 'error'>('info')
const forecastJobId = ref('')
const forecastResult = ref<any>(null)
const forecastWarnings = ref<string[]>([])

const backtestStart = ref('')
const backtestEnd = ref('')
const backtestRunning = ref(false)
const backtestTaskText = ref('尚未提交回测任务')
const backtestTaskType = ref<'info' | 'success' | 'error'>('info')
const backtestJobId = ref('')
const backtestResult = ref<any>(null)
const backtestWarnings = ref<string[]>([])

const chartEl = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let chartObserver: ResizeObserver | null = null
const timers: Record<'forecast' | 'backtest', number | null> = { forecast: null, backtest: null }

function applyDates() {
  const m = userStore.manifest || {}
  const end = m.date_max || ''
  origin.value = end
  const lastComplete = end ? addDays(end, -7) : ''
  const firstEligible = m.date_min ? addDays(m.date_min, 27) : ''
  const start = lastComplete ? addDays(lastComplete, -27) : ''
  backtestEnd.value = lastComplete && firstEligible && lastComplete >= firstEligible ? lastComplete : ''
  backtestStart.value = backtestEnd.value ? (start < firstEligible ? firstEligible : start) : ''
}

function setForecastTask(text: string, type: 'info' | 'success' | 'error') {
  forecastTaskText.value = text
  forecastTaskType.value = type
}
function setBacktestTask(text: string, type: 'info' | 'success' | 'error') {
  backtestTaskText.value = text
  backtestTaskType.value = type
}

function pollJob(kind: 'forecast' | 'backtest', jobId: string, onSuccess: (job: any) => void, updateTask: (t: string, k: 'info' | 'success' | 'error') => void) {
  function finish() {
    if (timers[kind] !== null) window.clearInterval(timers[kind]!)
    timers[kind] = null
    if (kind === 'forecast') forecastRunning.value = false
    else backtestRunning.value = false
  }
  if (timers[kind] !== null) window.clearInterval(timers[kind]!)
  let tries = 0
  timers[kind] = window.setInterval(async () => {
    tries += 1
    if (tries > 400) {
      updateTask('轮询超时，请稍后重试', 'error')
      finish()
      return
    }
    try {
      const data = await ece.job(jobId)
      const job = data.job || data
      if (job.status === 'succeeded') {
        finish()
        const label = kind === 'forecast' ? '预测已生成' : '回测已完成'
        const duration = job.elapsed_seconds != null ? ` · ${fmt(job.elapsed_seconds, 2)} 秒` : ''
        updateTask(label + duration, 'success')
        let result = job.result
        if (typeof result === 'string') {
          try {
            result = JSON.parse(result)
          } catch {
            result = null
          }
        }
        if (!result) {
          ElMessage.error('任务成功但结果为空')
          return
        }
        onSuccess({ job, result })
      } else if (job.status === 'failed') {
        finish()
        updateTask(`任务失败：${job.error || '未知错误'}`, 'error')
        ElMessage.error(`任务失败：${job.error || '未知错误'}`)
      } else {
        updateTask(job.status === 'queued' ? '任务已排队' : '正在计算…', 'info')
      }
    } catch (error: any) {
      finish()
      updateTask(`读取任务失败：${error.message}`, 'error')
    }
  }, 1200)
}

async function runForecast() {
  if (forecastRunning.value) return
  if (!origin.value) {
    ElMessage.warning('请选择预测起点')
    return
  }
  forecastRunning.value = true
  forecastResult.value = null
  setForecastTask('正在提交预测任务…', 'info')
  try {
    const data = await ece.forecast({ origin: origin.value })
    forecastJobId.value = data.job_id
    setForecastTask('预测任务已排队', 'info')
    pollJob(
      'forecast',
      data.job_id,
      ({ job, result }) => {
        forecastResult.value = { ...result, jobBatch: job.batch }
        forecastWarnings.value = result.warnings || []
      },
      setForecastTask,
    )
  } catch (error: any) {
    setForecastTask(`提交失败：${error.message}`, 'error')
    forecastRunning.value = false
  }
}

async function runBacktest() {
  if (backtestRunning.value) return
  if (!backtestStart.value || !backtestEnd.value) {
    ElMessage.warning('请选择回测起止日期')
    return
  }
  if (backtestStart.value > backtestEnd.value) {
    ElMessage.warning('回测首个起点不能晚于最后起点')
    return
  }
  if (daysBetween(backtestStart.value, backtestEnd.value) > 55) {
    ElMessage.warning('回测起点区间须为 1 至 56 个日历日')
    return
  }
  backtestRunning.value = true
  backtestResult.value = null
  setBacktestTask('正在提交回测任务…', 'info')
  try {
    const data = await ece.backtest({
      start: backtestStart.value,
      end: backtestEnd.value,
    })
    backtestJobId.value = data.job_id
    setBacktestTask('回测任务已排队', 'info')
    pollJob(
      'backtest',
      data.job_id,
      ({ job, result }) => {
        backtestResult.value = { ...result, jobBatch: job.batch }
        backtestWarnings.value = result.warnings || []
      },
      setBacktestTask,
    )
  } catch (error: any) {
    setBacktestTask(`提交失败：${error.message}`, 'error')
    backtestRunning.value = false
  }
}

function disposeChart() {
  chartObserver?.disconnect()
  chartObserver = null
  chart?.dispose()
  chart = null
}

function drawForecastChart() {
  const element = chartEl.value
  if (!element || !forecastResult.value) return
  if (!chart || chart.getDom() !== element) {
    disposeChart()
    chart = echarts.init(element)
    chartObserver = new ResizeObserver(() => chart?.resize())
    chartObserver.observe(element)
  }
  const rows = forecastResult.value.rows || []
  chart.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { top: 0, data: ['模型预测', '近四周同星期均值', '实际销量'] },
      grid: { left: 56, right: 20, top: 34, bottom: 34 },
      xAxis: { type: 'category', data: rows.map((r: any) => r.date), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#edf1e8', type: 'dashed' } } },
      series: [
        {
          name: '模型预测',
          type: 'line',
          data: rows.map((r: any) => r.prediction),
          lineStyle: { color: '#167967', width: 2.4 },
          itemStyle: { color: '#167967' },
          showSymbol: true,
        },
        {
          name: '近四周同星期均值',
          type: 'line',
          data: rows.map((r: any) => r.baseline),
          lineStyle: { color: '#ad7927', type: 'dashed' },
          itemStyle: { color: '#ad7927' },
          showSymbol: false,
        },
        {
          name: '实际销量',
          type: 'line',
          data: rows.map((r: any) => (r.actual === null ? null : r.actual)),
          lineStyle: { color: '#4a6169', width: 2 },
          itemStyle: { color: '#4a6169' },
          showSymbol: true,
        },
      ],
    },
    true,
  )
}

function exportForecast() {
  if (!forecastJobId.value) return
  download(`/api/jobs/${encodeURIComponent(forecastJobId.value)}/export`, 'forecast.csv')
}
function exportBacktest() {
  if (!backtestJobId.value) return
  download(`/api/jobs/${encodeURIComponent(backtestJobId.value)}/export`, 'backtest.csv')
}

const forecastBatchLabel = computed(
  () => forecastResult.value?.jobBatch?.label || forecastResult.value?.batch?.label || userStore.batch?.label || '—',
)
const backtestBatchLabel = computed(
  () => backtestResult.value?.jobBatch?.label || backtestResult.value?.batch?.label || userStore.batch?.label || '—',
)
const horizonEntries = computed(() => {
  const by = backtestResult.value?.by_horizon || {}
  return [1, 2, 3, 4, 5, 6, 7].map(h => ({ h, ...(by[String(h)] || {}) }))
})
const predictionSummary = computed(() => {
  const rows = (forecastResult.value?.rows || []) as {
    date: string; prediction: number; baseline: number; actual: number | null
  }[]
  const total = rows.reduce((sum, row) => sum + row.prediction, 0)
  const baseline = rows.reduce((sum, row) => sum + row.baseline, 0)
  const peak = rows.length ? rows.reduce((highest, row) => row.prediction > highest.prediction ? row : highest) : null
  return { total, baseline, peak }
})
const metricDefinitions = [
  { key: 'WAPE', label: '加权绝对误差率', description: '日绝对误差之和 ÷ 实际销量之和', rate: true },
  { key: 'MAE', label: '平均绝对误差', description: '每条日预测平均相差多少件' },
  { key: 'RMSE', label: '均方根误差', description: '对误差较大的日期更敏感，单位为件' },
  { key: 'MEDIAN_AE', label: '绝对误差中位数', description: '一半预测的误差不超过这个件数' },
  { key: 'P90_AE', label: '90 分位绝对误差', description: '约九成预测的误差不超过这个件数' },
  { key: 'BIAS_PCT', label: '销量偏差率', description: '正值表示整体高估，负值表示整体低估', rate: true },
] as const
const comparisonRows = computed(() => {
  const result = backtestResult.value
  const complete = Boolean(result?.complete_origins)
  const model = complete ? result?.complete_window_metrics : result?.metrics
  const baseline = complete ? result?.complete_window_baseline_metrics : result?.baseline_metrics
  return [
    ...metricDefinitions.map(metric => {
      const render = (value: unknown) => 'rate' in metric ? pct(value, 2) : fmt(value, 2)
      return { ...metric, model: render(model?.[metric.key]), baseline: render(baseline?.[metric.key]) }
    }),
    {
      key: 'WEEKLY_WAPE',
      label: '7 天总量绝对误差率',
      description: '先逐窗口合计 7 天销量，再计算 WAPE',
      model: pct(result?.weekly_total_metrics?.WAPE, 2),
      baseline: pct(result?.weekly_total_baseline_metrics?.WAPE, 2),
    },
  ]
})
const comparison = computed(() => {
  const result = backtestResult.value
  const model = result?.complete_origins ? result.complete_window_metrics : result?.metrics
  const baseline = result?.complete_origins ? result.complete_window_baseline_metrics : result?.baseline_metrics
  if (model?.WAPE == null || baseline?.WAPE == null) return ''
  const gap = Math.round((model.WAPE - baseline.WAPE) * 10000) / 100
  if (!gap) return '模型与基准的 WAPE 持平'
  return `模型 WAPE 比基准${gap > 0 ? '高' : '低'} ${fmt(Math.abs(gap), 2)} 个百分点`
})

onMounted(async () => {
  if (!userStore.manifest.date_max) {
    await userStore.loadOptions()
  }
  applyDates()
  try {
    const saved = await ece.latestForecast()
    const priorForecast = saved.jobs?.forecast
    if (priorForecast?.result) {
      forecastJobId.value = priorForecast.id
      forecastResult.value = { ...priorForecast.result, jobBatch: priorForecast.batch }
      forecastWarnings.value = priorForecast.result.warnings || []
      origin.value = priorForecast.result.origin
      setForecastTask('已载入当前批次上次成功的预测', 'success')
    }
    const priorBacktest = saved.jobs?.backtest
    if (priorBacktest?.result) {
      backtestJobId.value = priorBacktest.id
      backtestResult.value = { ...priorBacktest.result, jobBatch: priorBacktest.batch }
      backtestWarnings.value = priorBacktest.result.warnings || []
      backtestStart.value = priorBacktest.result.start
      backtestEnd.value = priorBacktest.result.end
      setBacktestTask('已载入当前批次上次成功的回测', 'success')
    }
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`已保存预测读取失败：${error.message}`)
  }
})

onBeforeUnmount(() => {
  for (const timer of Object.values(timers)) if (timer !== null) window.clearInterval(timer)
  disposeChart()
})

watch([chartEl, forecastResult], ([element, result]) => {
  if (!element || !result) {
    disposeChart()
    return
  }
  drawForecastChart()
}, { flush: 'post' })
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <h1>预测与回测</h1>
        <p>查看全店未来 7 天销量，并用历史实际销量评估预测误差。</p>
      </div>
    </div>

    <details class="training-context">
      <summary>数据划分与回测口径</summary>
      <p v-if="userStore.batch?.id === 'simulation_20111101'">
        模拟日期为 2011-11-01，已知交易截至 2011-10-31。训练期为 2009-12-01 至 2011-08-15，
        验证期为 2011-08-16 至 2011-10-14，已观察的测试期为 2011-10-15 至 2011-10-31。
      </p>
      <p v-else-if="userStore.batch?.id === 'original'">
        完整原始批次包含 2011-11-01 之后的交易，树模型训练上限为 2011-08-15。
      </p>
      <p v-else>当前导入批次使用自选区间滚动回测，未登记单独的训练、验证和测试时段。</p>
      <p>每个回测起点只使用当时可见的历史。无记录日按 0 构造连续销量，实际值缺失的未来日暂不评分。</p>
    </details>

    <div class="forecast-layout">
      <div class="ece-panel forecast-controls">
        <div class="ece-panel-head"><h2>预测设置</h2></div>
        <el-form label-position="top">
          <p class="ece-small ece-muted model-desc">已启用统一销量预测模型。选择日期后生成预测即可。</p>
          <el-form-item label="预测起点">
            <el-date-picker v-model="origin" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-button type="primary" style="width: 100%" :loading="forecastRunning" @click="runForecast">
            生成未来 7 天预测
          </el-button>
          <el-alert
            :title="forecastTaskText"
            :type="forecastTaskType"
            :closable="false"
            show-icon
            class="task-state"
          />
        </el-form>

        <el-divider />
        <div class="ece-panel-head"><h2>滚动起点回测</h2></div>
        <el-form label-position="top">
          <p class="ece-small ece-muted" style="margin: 0 0 8px">默认选取末段28个有完整7天实际值的起点；可自选1—56个起点。重叠预测不是独立样本。</p>
          <el-form-item label="首个起点">
            <el-date-picker v-model="backtestStart" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="最后起点">
            <el-date-picker v-model="backtestEnd" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-button style="width: 100%" :loading="backtestRunning" @click="runBacktest">运行历史回测</el-button>
          <el-alert
            :title="backtestTaskText"
            :type="backtestTaskType"
            :closable="false"
            show-icon
            class="task-state"
          />
        </el-form>
      </div>

      <div class="forecast-results">
        <div v-if="!forecastResult && !backtestResult" class="ece-panel ece-empty">
          先选择预测起点并生成预测，或运行历史回测
        </div>

        <template v-if="forecastResult">
          <div class="ece-panel">
            <div class="ece-panel-head">
              <div>
                <h2>未来 7 天预测</h2>
                <p>
                  {{ forecastResult.rows?.[0]?.date }} 至 {{ forecastResult.rows?.at(-1)?.date }} · 全店记录销量
                </p>
              </div>
              <el-button @click="exportForecast">↓ 导出结果 CSV</el-button>
            </div>
            <div class="ece-kpi-grid">
              <div class="ece-kpi">
                <div class="ece-kpi__label">7 天预测合计 · 件</div>
                <div class="ece-kpi__value">{{ fmt(predictionSummary.total, 2) }}</div>
                <div class="ece-kpi__foot">近四周同星期均值合计 {{ fmt(predictionSummary.baseline, 2) }} 件</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">日均预测 · 件</div>
                <div class="ece-kpi__value">{{ fmt(predictionSummary.total / forecastResult.horizon, 2) }}</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">预计销量最高日 · 件</div>
                <div class="ece-kpi__value">{{ fmt(predictionSummary.peak?.prediction, 2) }}</div>
                <div class="ece-kpi__foot">{{ predictionSummary.peak?.date }}</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">有实际值天数</div>
                <div class="ece-kpi__value">{{ fmt(forecastResult.actual_days) }} / {{ fmt(forecastResult.horizon || 7) }}</div>
                <div class="ece-kpi__foot">
                  {{ forecastResult.actual_days ? '误差仅按已有实际值计算' : '未来销量尚未发生，暂无法评分' }}
                </div>
              </div>
            </div>
            <p v-if="forecastResult.actual_days" class="foot-note">
              已有实际值的目标日：WAPE {{ pct(forecastResult.metrics?.WAPE, 2) }} ·
              平均绝对误差 {{ fmt(forecastResult.metrics?.MAE, 2) }} 件 ·
              销量偏差率 {{ pct(forecastResult.metrics?.BIAS_PCT, 2) }}
            </p>
            <div ref="chartEl" class="ece-chart" />
            <el-table :data="forecastResult.rows || []" size="small" style="margin-top: 12px">
              <el-table-column label="日期" prop="date" width="120" />
              <el-table-column label="预测（件）" min-width="110" align="right">
                <template #default="{ row }">{{ fmt(row.prediction, 2) }}</template>
              </el-table-column>
              <el-table-column label="近四周基准（件）" min-width="130" align="right">
                <template #default="{ row }">{{ fmt(row.baseline, 2) }}</template>
              </el-table-column>
              <el-table-column label="实际（件）" min-width="100" align="right">
                <template #default="{ row }">{{ fmt(row.actual, 2) }}</template>
              </el-table-column>
              <el-table-column label="预测−实际（件）" min-width="130" align="right">
                <template #default="{ row }">{{ row.actual == null ? '—' : fmt(row.prediction - row.actual, 2) }}</template>
              </el-table-column>
            </el-table>
            <details class="foot-note">
              <summary>模型、批次与口径</summary>
              <p>
                {{ forecastResult.method_label }} · {{ forecastResult.model_version }} · {{ forecastBatchLabel }}。
                历史 {{ forecastResult.history_start }} 至 {{ forecastResult.history_end }}，
                树模型训练截至 {{ forecastResult.training_end || '—' }}（{{ fmt(forecastResult.training_rows) }} 条）。
              </p>
              <ul v-if="forecastWarnings.length"><li v-for="w in forecastWarnings" :key="w">{{ w }}</li></ul>
            </details>
          </div>
        </template>

        <template v-if="backtestResult">
          <div class="ece-panel">
            <div class="ece-panel-head">
              <div>
                <h2>回测结果摘要</h2>
                <p>
                  起点区间 {{ backtestResult.start }} ~ {{ backtestResult.end }} · 版本 {{ backtestResult.model_version }} · 批次
                  {{ backtestBatchLabel }}
                </p>
              </div>
              <el-button @click="exportBacktest">↓ 导出回测 CSV</el-button>
            </div>
            <p v-if="comparison" class="comparison">{{ comparison }}</p>
            <p class="foot-note">
              {{ backtestResult.complete_origins ? '对照表使用有完整 7 天实际值的窗口' : '对照表使用全部有实际值的预测条目' }}。
              完整窗口 {{ fmt(backtestResult.complete_origins) }} / {{ fmt(backtestResult.origins_attempted) }} 个，
              全部可评分 {{ fmt(backtestResult.scored_count) }} 条<span v-if="backtestResult.scored_unique_days != null">，覆盖 {{ fmt(backtestResult.scored_unique_days) }} 个实际日期</span>。
            </p>
            <p v-if="!backtestResult.weekly_total_metrics" class="foot-note">重新运行回测可查看新增指标。</p>
            <el-table :data="comparisonRows" size="small">
              <el-table-column label="指标" min-width="235">
                <template #default="{ row }">
                  <strong>{{ row.label }}</strong><small class="metric-desc">{{ row.description }}</small>
                </template>
              </el-table-column>
              <el-table-column label="模型" prop="model" min-width="100" align="right" />
              <el-table-column label="近四周同星期基准" prop="baseline" min-width="150" align="right" />
            </el-table>
            <details v-if="backtestWarnings.length" class="foot-note">
              <summary>回测口径与提示</summary>
              <ul><li v-for="w in backtestWarnings" :key="w">{{ w }}</li></ul>
            </details>
            <div class="ece-panel-head" style="margin-top: 8px">
              <div>
                <h2>分步长误差（WAPE）</h2>
                <p>全部有实际值的预测，按目标日离起点的天数分组</p>
              </div>
            </div>
            <div class="horizon-grid">
              <div v-for="e in horizonEntries" :key="e.h" class="horizon-item">
                <span>第 {{ e.h }} 天</span>
                <strong>{{ pct(e.WAPE, 2) }}</strong>
                <small>MAE {{ fmt(e.MAE, 2) }} 件 · {{ fmt(e.n) }} 条</small>
              </div>
            </div>
            <div class="ece-panel-head" style="margin-top: 14px">
              <div>
                <h2>高误差案例</h2>
                <p>每个目标日保留绝对误差最大的一条，前 5 条</p>
              </div>
            </div>
            <el-table :data="backtestResult.worst_cases || []" size="small">
              <el-table-column label="目标日" prop="date" width="120" />
              <el-table-column label="起点" prop="origin" width="120" />
              <el-table-column label="第几天" prop="h" width="70" align="center" />
              <el-table-column label="实际（件）" min-width="100" align="right">
                <template #default="{ row }">{{ fmt(row.actual, 2) }}</template>
              </el-table-column>
              <el-table-column label="预测（件）" min-width="110" align="right">
                <template #default="{ row }">{{ fmt(row.prediction, 2) }}</template>
              </el-table-column>
              <el-table-column label="绝对误差（件）" min-width="120" align="right">
                <template #default="{ row }">{{ fmt(row.abs_error, 2) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.training-context { margin: 0 0 15px; padding: 8px 12px; color: #4c6057; background: #f2f6f1; border-radius: 8px; font-size: 12px; line-height: 1.7; }
.training-context summary { width: fit-content; cursor: pointer; font-weight: 600; }
.training-context p { margin: 6px 0 0; }
.forecast-layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 16px; align-items: start; }
.forecast-controls { position: sticky; top: 118px; }
.forecast-results { min-width: 0; }
.model-desc { margin-top: 6px; line-height: 1.7; }
.task-state { margin-top: 10px; }
.horizon-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; }
.horizon-item { border: 1px solid #e5e9e5; border-radius: 8px; padding: 8px 6px; text-align: center; background: #fbfcfa; }
.horizon-item span, .horizon-item small { display: block; font-size: 9.5px; color: #758487; }
.horizon-item strong { display: block; font-size: 14px; margin: 6px 0; color: #167967; }
.foot-note { color: #8a9a92; font-size: 11px; line-height: 1.8; padding-top: 10px; }
.foot-note summary { cursor: pointer; width: fit-content; }
.comparison { color: #15594b; font-weight: 600; margin: 12px 0 0; }
.metric-desc { display: block; color: #586a65; font-size: 11px; }
@media (max-width: 1100px) {
  .forecast-layout { grid-template-columns: minmax(0, 1fr); }
  .forecast-controls { position: static; }
  .horizon-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
</style>
