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
      updateTask(
        `任务 ${jobId}：${job.status}${job.elapsed_seconds != null ? ` · ${fmt(job.elapsed_seconds, 1)}s` : ''}`,
        'info',
      )
      if (job.status === 'succeeded') {
        finish()
        updateTask(`任务成功：${jobId}${job.elapsed_seconds != null ? ` · ${fmt(job.elapsed_seconds, 1)}s` : ''}`, 'success')
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
    setForecastTask(`任务已排队：${data.job_id} · 批次 ${data.batch_id || '—'}`, 'info')
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
    setBacktestTask(`任务已排队：${data.job_id} · 批次 ${data.batch_id || '—'}`, 'info')
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
      legend: { top: 0, data: ['预测', '实际'] },
      grid: { left: 56, right: 20, top: 34, bottom: 34 },
      xAxis: { type: 'category', data: rows.map((r: any) => r.date), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#edf1e8', type: 'dashed' } } },
      series: [
        {
          name: '预测',
          type: 'line',
          data: rows.map((r: any) => r.prediction),
          lineStyle: { color: '#167967', width: 2.4 },
          itemStyle: { color: '#167967' },
          showSymbol: true,
        },
        {
          name: '实际',
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
        <div class="ece-eyebrow">FORECAST &amp; BACKTEST</div>
        <h1>预测与回测</h1>
        <p>系统统一生成全店未来7天销量预测，自动减弱偶发大单对常规趋势的放大。</p>
      </div>
      <el-tag effect="plain">范围：全店 · horizon 7</el-tag>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="预测基于历史记录与固定参数模型实验，不是经营收益预测；无记录日按 0 构造连续序列。WAPE/MAE 是历史回测误差，不能解释为提升收益。"
      class="mb12"
    />

    <el-alert
      v-if="userStore.batch?.id === 'original'"
      type="warning"
      :closable="false"
      show-icon
      class="mb12"
    >
      <template #title>Online Retail II 历史基准实验已按时间顺序分为训练、验证和测试时期</template>
      <div>
        训练：2009-12-01 至 2011-08-15；验证：2011-08-16 至 2011-10-14，用于确定模型与稳健处理参数；
        历史回溯：2011-10-15 至 2011-12-09。该段已经观察过，不能再称全新盲测。每个起点仅使用当时可见的数据。
        下方自选起点回测是另一段动态评估；若据此反复选模型或改参数，该区间就不再是独立测试集。
      </div>
    </el-alert>

    <el-alert
      v-else
      type="warning"
      :closable="false"
      show-icon
      class="mb12"
      title="当前导入批次没有单独登记的训练、验证和测试日期。下方为自选区间滚动回测，不会自动留出独立测试集；确定模型或调参数后，应另留未参与选择的最后一段日期作最终测试。"
    />

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
                  起点 {{ forecastResult.origin }} · 版本
                  {{ forecastResult.model_version }} · 批次 {{ forecastBatchLabel }}
                </p>
              </div>
              <el-button @click="exportForecast">↓ 导出结果 CSV</el-button>
            </div>
            <div class="ece-warnings">
              <el-alert v-for="(w, i) in forecastWarnings" :key="i" :title="w" type="warning" :closable="false" show-icon />
            </div>
            <div class="ece-kpi-grid">
              <div class="ece-kpi">
                <div class="ece-kpi__label">WAPE</div>
                <div class="ece-kpi__value">{{ pct(forecastResult.metrics?.WAPE) }}</div>
                <div class="ece-kpi__foot">实际值齐备后评价</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">MAE</div>
                <div class="ece-kpi__value">{{ fmt(forecastResult.metrics?.MAE, 2) }}</div>
                <div class="ece-kpi__foot">平均每天绝对误差 · 件</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">偏差 BIAS</div>
                <div class="ece-kpi__value">{{ fmt(forecastResult.metrics?.BIAS, 2) }}</div>
                <div class="ece-kpi__foot">偏差率：{{ pct(forecastResult.metrics?.BIAS_PCT) }}</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">有实际值天数</div>
                <div class="ece-kpi__value">{{ fmt(forecastResult.actual_days) }} / {{ fmt(forecastResult.horizon || 7) }}</div>
                <div class="ece-kpi__foot">{{ forecastResult.complete_actual ? '7 天实际值完整' : '末端日期无实际值，不参与评分' }}</div>
              </div>
            </div>
            <div ref="chartEl" class="ece-chart" />
            <el-table :data="forecastResult.rows || []" size="small" style="margin-top: 12px">
              <el-table-column label="日期" prop="date" width="120" />
              <el-table-column label="h" prop="h" width="60" align="center" />
              <el-table-column label="预测" width="110" align="right">
                <template #default="{ row }">{{ fmt(row.prediction, 1) }}</template>
              </el-table-column>
              <el-table-column label="实际" width="110" align="right">
                <template #default="{ row }">{{ row.actual === null ? '—' : fmt(row.actual) }}</template>
              </el-table-column>
              <el-table-column label="绝对误差" align="right">
                <template #default="{ row }">{{ row.abs_error === null ? '—' : fmt(row.abs_error, 1) }}</template>
              </el-table-column>
            </el-table>
            <div class="foot-note">
              历史：{{ forecastResult.history_start }} ~ {{ forecastResult.history_end }}（{{ fmt(forecastResult.history_days) }} 天）·
              训练行数 {{ fmt(forecastResult.training_rows) }} · 批次 {{ forecastBatchLabel }} · 固定模型实验，不代表经营收益。
            </div>
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
            <div class="ece-warnings">
              <el-alert v-for="(w, i) in backtestWarnings" :key="i" :title="w" type="warning" :closable="false" show-icon />
            </div>
            <div class="ece-kpi-grid">
              <div class="ece-kpi">
                <div class="ece-kpi__label">完整7天窗口 WAPE</div>
                <div class="ece-kpi__value">{{ pct(backtestResult.complete_window_metrics?.WAPE) }}</div>
                <div class="ece-kpi__foot">{{ fmt(backtestResult.complete_window_metrics?.n) }} 条完整窗口评分</div>
                <div class="ece-kpi__foot">全部可评分目标 WAPE：{{ pct(backtestResult.metrics?.WAPE) }}</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">MAE</div>
                <div class="ece-kpi__value">{{ fmt(backtestResult.metrics?.MAE, 2) }}</div>
                <div class="ece-kpi__foot">全部可评分目标 · 件</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">偏差率</div>
                <div class="ece-kpi__value">{{ pct(backtestResult.metrics?.BIAS_PCT) }}</div>
                <div class="ece-kpi__foot">平均 BIAS：{{ fmt(backtestResult.metrics?.BIAS, 2) }}</div>
                <div class="ece-kpi__foot">预测−实际：正值为高估，负值为低估</div>
              </div>
              <div class="ece-kpi">
                <div class="ece-kpi__label">评分目标数</div>
                <div class="ece-kpi__value">{{ fmt(backtestResult.scored_count) }}</div>
                <div class="ece-kpi__foot">
                  起点 {{ fmt(backtestResult.origins_attempted) }} 个 · 完整窗口 {{ fmt(backtestResult.complete_origins) }}
                </div>
              </div>
            </div>
            <div class="ece-panel-head" style="margin-top: 8px">
              <div>
                <h2>分步长误差（WAPE）</h2>
                <p>h=1..7，按目标日相对起点计</p>
              </div>
            </div>
            <div class="horizon-grid">
              <div v-for="e in horizonEntries" :key="e.h" class="horizon-item">
                <span>h={{ e.h }}</span>
                <strong>{{ pct(e.WAPE) }}</strong>
                <small>MAE {{ fmt(e.MAE, 1) }} · n {{ fmt(e.n) }}</small>
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
              <el-table-column label="h" prop="h" width="60" align="center" />
              <el-table-column label="实际" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.actual) }}</template>
              </el-table-column>
              <el-table-column label="预测" width="110" align="right">
                <template #default="{ row }">{{ fmt(row.prediction, 1) }}</template>
              </el-table-column>
              <el-table-column label="绝对误差" align="right">
                <template #default="{ row }">{{ fmt(row.abs_error, 1) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mb12 { margin-bottom: 12px; }
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
@media (max-width: 1100px) {
  .forecast-layout { grid-template-columns: minmax(0, 1fr); }
  .forecast-controls { position: static; }
  .horizon-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
</style>
