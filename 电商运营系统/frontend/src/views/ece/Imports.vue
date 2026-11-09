<script lang="ts" setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { download, ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { fmt, isoTime } from '@/utils/format'

defineOptions({ name: 'EceImports' })

const userStore = useUserStore()

const file = ref<File | null>(null)
const label = ref('')
const submitting = ref(false)
const jobId = ref('')
const jobData = ref<any>(null)
const polling = ref(false)
const batches = ref<any[]>([])
const batchesLoading = ref(false)
const activatingId = ref('')
let timer: number | null = null

function onFileChange(uploadFile: UploadFile) {
  file.value = (uploadFile.raw as File) || null
}

async function submitImport() {
  if (!file.value) {
    ElMessage.warning('请选择要导入的 .xlsx / .xls / .csv 文件')
    return
  }
  submitting.value = true
  jobData.value = null
  try {
    const data = await ece.importSubmit(file.value, label.value.trim())
    jobId.value = data.job_id
    ElMessage.success(`导入任务已提交：${data.status || 'queued'}`)
    startPolling()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`导入提交失败：${error.message}`)
  } finally {
    submitting.value = false
  }
}

function stopPolling() {
  polling.value = false
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

function startPolling() {
  stopPolling()
  polling.value = true
  let tries = 0
  timer = window.setInterval(async () => {
    tries += 1
    if (tries > 300) {
      stopPolling()
      ElMessage.warning('轮询超时，请稍后刷新批次列表确认结果')
      return
    }
    try {
      const data = await ece.importJob(jobId.value)
      jobData.value = data
      const status = data?.job?.status
      if (status === 'succeeded' || status === 'failed' || status === 'duplicate') {
        stopPolling()
        await loadBatches()
        const text =
          status === 'succeeded'
            ? '导入成功，批次已生成（未自动切换）'
            : status === 'duplicate'
              ? '重复文件：已存在同 SHA-256 批次'
              : `导入失败：${data?.job?.error || '未知错误'}`
        if (status === 'failed') ElMessage.error(text)
        else ElMessage.success(text)
      }
    } catch (error: any) {
      stopPolling()
      if (error?.status !== 401) ElMessage.error(`读取导入任务失败：${error.message}`)
    }
  }, 1200)
}

async function loadBatches() {
  batchesLoading.value = true
  try {
    const data = await ece.batches()
    batches.value = data.batches || []
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`批次列表读取失败：${error.message}`)
  } finally {
    batchesLoading.value = false
  }
}

async function activateBatch(batch: any) {
  activatingId.value = batch.id
  try {
    await ece.activateBatch(batch.id)
    ElMessage.success(`已切换批次：${batch.label || batch.id}`)
    await userStore.loadOptions()
    await loadBatches()
    if (jobData.value?.job?.id) {
      try {
        jobData.value = await ece.importJob(jobData.value.job.id)
      } catch {
        /* 旧任务不可读时忽略 */
      }
    }
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`切换批次失败：${error.message}`)
  } finally {
    activatingId.value = ''
  }
}

function batchCoverage(batch: any) {
  const m = batch?.manifest || {}
  return m.date_min && m.date_max ? `${m.date_min} ~ ${m.date_max}` : '—'
}

async function downloadTemplate() {
  try {
    await download(ece.templateUrl, 'import_template.csv')
  } catch {
    /* download 内部已提示 */
  }
}

const reportRows = (report: Record<string, any>) =>
  [
    ['原始行数', report.raw_rows],
    ['保留行数', report.retained_rows],
    ['正向销售行', report.positive_sale_rows],
    ['跨表副本已排除', report.cross_sheet_copies_excluded],
    ['表内重复候选保留', report.within_sheet_duplicate_candidates_retained],
    ['取消行', report.cancelled_rows],
    ['非正数量行', report.nonpositive_quantity_rows],
    ['非法日期', report.invalid_dates],
    ['数量非法行', report.quantity_invalid_rows],
    ['无有效单价行', report.sale_rows_without_valid_price],
    ['零单价销售行', report.zero_price_sale_rows],
    ['日期范围', report.date_min && report.date_max ? `${report.date_min} ~ ${report.date_max}` : ''],
    ['口径版本', report.policy_version],
    ['耗时', report.elapsed_seconds !== undefined ? `${fmt(report.elapsed_seconds, 2)} 秒` : ''],
  ].filter(([, v]) => v !== undefined && v !== null && v !== '')

onMounted(loadBatches)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <div class="ece-eyebrow">DATA IMPORTS</div>
        <h1>数据导入</h1>
        <p>上传 xlsx / xls / csv，后台导入并生成独立批次；成功后需手动切换为当前批次。</p>
      </div>
      <el-button @click="downloadTemplate">↓ 下载示例模板</el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="导入不会覆盖当前批次，也不自动切换；同一文件（SHA-256 相同）重复导入会直接返回 duplicate。上传上限 20MB。"
      class="mb12"
    />

    <div class="ece-cards-2">
      <div class="ece-panel">
        <div class="ece-panel-head"><h2>上传文件</h2></div>
        <el-form label-position="top">
          <el-form-item label="数据文件（.xlsx / .xls / .csv）">
            <el-upload
              :auto-upload="false"
              :limit="1"
              accept=".xlsx,.xls,.csv"
              :on-change="onFileChange"
              :on-exceed="() => ElMessage.warning('一次只上传一个文件')"
            >
              <el-button>选择文件</el-button>
            </el-upload>
            <div class="ece-small ece-muted" style="margin-top: 6px">
              {{ file ? `已选择：${file.name}` : '尚未选择文件' }}
            </div>
          </el-form-item>
          <el-form-item label="批次标签（可选）">
            <el-input v-model="label" placeholder="例如 2026-11 上传样例" />
          </el-form-item>
          <el-button type="primary" :loading="submitting" @click="submitImport">开始导入</el-button>
        </el-form>
        <div v-if="jobId" class="import-status">
          <el-tag :type="polling ? 'warning' : jobData?.job?.status === 'succeeded' ? 'success' : 'info'" size="small">
            {{ polling ? '轮询中' : jobData?.job?.status || 'queued' }}
          </el-tag>
          <span class="ece-mono">{{ jobId }}</span>
        </div>
      </div>

      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>最近导入任务</h2>
            <p>状态：queued / running / succeeded / failed / duplicate</p>
          </div>
        </div>
        <div v-if="jobData?.job">
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="状态">
              <el-tag
                size="small"
                :type="jobData.job.status === 'failed' ? 'danger' : jobData.job.status === 'succeeded' ? 'success' : 'info'"
              >
                {{ jobData.job.status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="文件">{{ jobData.job.file_name }}</el-descriptions-item>
            <el-descriptions-item label="创建 / 完成">
              {{ isoTime(jobData.job.created_at) }} / {{ isoTime(jobData.job.finished_at) }}
            </el-descriptions-item>
            <el-descriptions-item v-if="jobData.job.error" label="错误">
              <span style="color: #b23b32">{{ jobData.job.error }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="reportRows(jobData.report || {}).length" class="report-grid">
            <span v-for="[k, v] in reportRows(jobData.report || {})" :key="String(k)" class="report-item">
              <em>{{ k }}</em><b>{{ v }}</b>
            </span>
          </div>
          <div v-if="jobData.issues?.length" style="margin-top: 10px">
            <div class="ece-panel-head">
              <h2>质量问题（{{ jobData.issues.length }} 条，显示前 100）</h2>
            </div>
            <el-table :data="jobData.issues.slice(0, 100)" size="small" max-height="240">
              <el-table-column label="级别" width="90" prop="level" />
              <el-table-column label="行" width="80" prop="row" />
              <el-table-column label="字段" width="120" prop="field" />
              <el-table-column label="说明" prop="message" min-width="200" />
            </el-table>
          </div>
          <el-alert
            v-if="jobData.batch"
            type="info"
            :closable="false"
            show-icon
            style="margin-top: 10px"
            :title="`批次：${jobData.batch.label || jobData.batch.id}（${jobData.batch.id}）${jobData.batch.is_active ? ' · 已是当前批次' : ' · 尚未切换'}`"
          />
          <el-button
            v-if="jobData.batch && !jobData.batch.is_active && jobData.job.status === 'succeeded'"
            type="primary"
            style="margin-top: 10px"
            :loading="activatingId === jobData.batch.id"
            @click="activateBatch(jobData.batch)"
          >
            切换为当前批次
          </el-button>
        </div>
        <div v-else class="ece-empty">尚未提交导入任务</div>
      </div>
    </div>

    <div class="ece-panel">
      <div class="ece-panel-head">
        <div>
          <h2>批次列表</h2>
          <p>仅一个批次为“当前批次”，切换后看板/订单/预测按新批次刷新</p>
        </div>
        <el-button :loading="batchesLoading" @click="loadBatches">刷新批次</el-button>
      </div>
      <el-table :data="batches" v-loading="batchesLoading" size="small">
        <el-table-column label="批次 ID" prop="id" min-width="200" />
        <el-table-column label="标签" prop="label" min-width="220" />
        <el-table-column label="类型" prop="kind" width="100" />
        <el-table-column label="覆盖范围" width="200">
          <template #default="{ row }">{{ batchCoverage(row) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ready' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="当前" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">当前</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button
              v-if="!row.is_active"
              size="small"
              :loading="activatingId === row.id"
              @click="activateBatch(row)"
            >
              切换
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.mb12 { margin-bottom: 12px; }
.import-status { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.report-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 14px; margin-top: 10px; }
.report-item { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; border-bottom: 1px dashed #eef1ec; padding-bottom: 4px; }
.report-item em { font-style: normal; color: #758487; }
.report-item b { font-weight: 600; color: #172c32; text-align: right; word-break: break-all; }
</style>
