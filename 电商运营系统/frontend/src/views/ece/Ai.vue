<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { download, ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { fmt, isoTime } from '@/utils/format'

defineOptions({ name: 'EceAi' })

const userStore = useUserStore()

const status = ref<any>(null)
const tools = ref<any[]>([])
const toolsPolicy = ref('')
const asking = ref(false)
const question = ref('最近30天销量最高的商品是什么？')
const scope = reactive({ start: '', end: '', product: '', country: '' })
const answer = ref<any>(null)
const briefLoading = ref(false)
const brief = ref<any>(null)

const llmConfigured = computed(() => Boolean(status.value?.llm_configured))
const batchLabel = computed(() => answer.value?.batch?.label || userStore.batch?.label || '—')
const rangeText = computed(() => {
  const range = answer.value?.range || {}
  const parts = [range.start, range.end].filter(Boolean)
  return parts.length ? parts.join(' ~ ') : '未指定（按工具默认）'
})

async function loadStatus() {
  try {
    status.value = await ece.aiStatus()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`AI 状态读取失败：${error.message}`)
  }
}

async function loadTools() {
  try {
    const data = await ece.aiTools()
    tools.value = data.tools || []
    toolsPolicy.value = data.policy || ''
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`AI 工具清单读取失败：${error.message}`)
  }
}

async function ask() {
  if (!question.value.trim()) {
    ElMessage.warning('请输入问题')
    return
  }
  asking.value = true
  answer.value = null
  try {
    const body: Record<string, unknown> = { question: question.value.trim() }
    const scopePayload: Record<string, unknown> = {}
    if (scope.start) scopePayload.start = scope.start
    if (scope.end) scopePayload.end = scope.end
    if (scope.product) scopePayload.product = scope.product.trim()
    if (scope.country) scopePayload.country = scope.country
    if (Object.keys(scopePayload).length) body.scope = scopePayload
    answer.value = await ece.aiAsk(body)
    // 提问可能改变范围，刷新一次当前批次信息（不强制）
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`AI 提问失败：${error.message}`)
  } finally {
    asking.value = false
  }
}

async function generateBrief() {
  briefLoading.value = true
  brief.value = null
  try {
    const body: Record<string, unknown> = {}
    if (scope.start || scope.end || scope.product || scope.country) {
      body.scope = {
        ...(scope.start ? { start: scope.start } : {}),
        ...(scope.end ? { end: scope.end } : {}),
        ...(scope.product ? { product: scope.product.trim() } : {}),
        ...(scope.country ? { country: scope.country } : {}),
      }
    }
    brief.value = await ece.aiBrief(body)
    ElMessage.success('运营简报已生成')
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`生成简报失败：${error.message}`)
  } finally {
    briefLoading.value = false
  }
}

async function downloadBrief() {
  const data = brief.value
  if (!data?.brief?.id) return
  await download(data.brief.url || `/api/ai/briefs/${data.brief.id}.md`, `${data.brief.title || 'brief'}.md`)
}

const statusLabel = computed(
  () =>
    ({ ok: '正常回答', need_clarification: '需要澄清', failed: '调用失败', not_configured: '未配置' })[
      answer.value?.status as string
    ] || answer.value?.status || '',
)
const statusType = computed(() => {
  const s = answer.value?.status
  if (s === 'ok') return 'success'
  if (s === 'failed') return 'danger'
  return 'warning'
})

onMounted(async () => {
  if (!userStore.options.countries?.length) await userStore.loadOptions()
  await Promise.all([loadStatus(), loadTools()])
})
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <div class="ece-eyebrow">AI ASSISTANT</div>
        <h1>AI 助手</h1>
        <p>对当前批次做只读问询；回答中的数字全部来自工具结果。</p>
      </div>
      <el-button @click="loadStatus">刷新状态</el-button>
    </div>

    <el-alert
      v-if="status && !llmConfigured"
      type="warning"
      :closable="false"
      show-icon
      class="mb12"
    >
      <template #title>
        <b>未配置真实大模型</b>
        <el-tag size="small" type="warning" style="margin-left: 8px">未调用大模型</el-tag>
      </template>
      <div class="banner-text">
        当前回答由本地规则工具层（provider=<b>{{ status.provider || 'local-rule-planner' }}</b>
        <span v-if="status.fallback">，fallback={{ status.fallback }}</span>）生成：仅把问题映射到声明的只读工具并引用工具数字，
        不伪造自然语言智能结论。密钥状态：{{ status.key_present ? '已配置（但缺少完整配置）' : '未配置' }} ·
        可用工具 {{ fmt(status.tools_count) }} 个。
      </div>
    </el-alert>
    <el-alert v-else-if="status" type="success" :closable="false" show-icon class="mb12">
      <template #title>
        <b>已配置真实大模型</b>
        <el-tag size="small" type="success" style="margin-left: 8px">provider={{ status.provider }}</el-tag>
      </template>
      <div class="banner-text">
        模型 <b>{{ status.model }}</b> · 超时 {{ status.timeout_seconds }}s · 密钥{{ status.key_present ? '已配置（页面不回显）' : '未配置' }} ·
        可用工具 {{ fmt(status.tools_count) }} 个。回答中的数字均来自只读工具结果。
      </div>
    </el-alert>
    <el-alert v-else type="info" :closable="false" show-icon class="mb12" title="正在读取 AI 配置状态…" />

    <div class="ece-cards-2">
      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>提问</h2>
            <p>范围与批次会随回答返回；工具仅只读</p>
          </div>
        </div>
        <el-form label-position="top">
          <el-form-item label="问题">
            <el-input v-model="question" type="textarea" :rows="3" placeholder="例如：最近30天销量最高的商品是什么？" />
          </el-form-item>
          <div class="two-col">
            <el-form-item label="范围开始（可选）">
              <el-date-picker v-model="scope.start" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-form-item label="范围结束（可选）">
              <el-date-picker v-model="scope.end" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </div>
          <div class="two-col">
            <el-form-item label="商品（可选）">
              <el-input v-model="scope.product" placeholder="商品编码" />
            </el-form-item>
            <el-form-item label="国家（可选）">
              <el-select v-model="scope.country" clearable placeholder="全部国家" style="width: 100%">
                <el-option v-for="c in userStore.options.countries" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </div>
          <el-button type="primary" :loading="asking" @click="ask">提交问题</el-button>
        </el-form>

        <el-divider />
        <div class="ece-panel-head"><h2>可用工具（只读）</h2></div>
        <ul class="tool-list">
          <li v-for="tool in tools" :key="tool.name">
            <b class="ece-mono">{{ tool.name }}</b> — {{ tool.description }}
            <el-tag v-if="tool.read_only" size="small" type="success">只读</el-tag>
          </li>
          <li v-if="toolsPolicy" class="ece-muted">策略：{{ toolsPolicy }}</li>
          <li v-if="!tools.length" class="ece-muted">工具清单加载中…</li>
        </ul>

        <el-divider />
        <el-button :loading="briefLoading" @click="generateBrief">生成运营简报（Markdown）</el-button>
        <div v-if="brief" class="brief-box">
          <p>
            <b>{{ brief.brief?.title }}</b> · {{ isoTime(brief.brief?.created_at) }} ·
            provider={{ brief.provider || 'template' }}
          </p>
          <pre class="brief-preview">{{ (brief.markdown || '').slice(0, 2200) }}{{ (brief.markdown || '').length > 2200 ? '\n…（完整内容请下载）' : '' }}</pre>
          <el-button size="small" @click="downloadBrief">↓ 下载 Markdown 简报</el-button>
        </div>
      </div>

      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>回答</h2>
            <p>含范围、批次、工具调用轨迹与警告</p>
          </div>
        </div>
        <div v-if="!answer" class="ece-empty">尚未提问</div>
        <template v-else>
          <div class="answer-meta">
            <el-tag :type="statusType" size="small">{{ statusLabel }}</el-tag>
            <span>provider：<b>{{ answer.provider || '—' }}</b></span>
            <span>大模型调用：<b>{{ answer.llm_used ? '是' : '否（本地规则工具层）' }}</b></span>
            <span>范围：{{ rangeText }}</span>
            <span>批次：{{ batchLabel }}</span>
            <span v-if="answer.elapsed_seconds != null">耗时：{{ fmt(answer.elapsed_seconds, 2) }}s</span>
          </div>
          <el-alert
            v-if="answer.clarification"
            type="info"
            :closable="false"
            show-icon
            :title="`需要补充信息：${answer.clarification}`"
            class="mt10"
          />
          <el-alert
            v-if="answer.error"
            type="error"
            :closable="false"
            show-icon
            :title="`错误：${answer.error}`"
            class="mt10"
          />
          <pre v-if="answer.answer" class="ece-answer mt10">{{ answer.answer }}</pre>
          <div v-if="answer.tool_calls?.length" class="mt10">
            <div class="ece-panel-head"><h2>工具调用轨迹（{{ answer.tool_calls.length }}）</h2></div>
            <div v-for="(call, i) in answer.tool_calls" :key="i" class="ece-tool-call">
              <div class="tool-head">
                <b class="ece-mono">{{ call.name }}</b>
                <el-tag :type="call.ok ? 'success' : 'danger'" size="small">{{ call.ok ? '成功' : '失败' }}</el-tag>
              </div>
              <div class="params">参数：{{ JSON.stringify(call.params || {}) }}</div>
              <div class="summary">{{ call.summary || '—' }}</div>
            </div>
          </div>
          <div v-if="answer.warnings?.length" class="mt10">
            <el-alert
              v-for="(w, i) in answer.warnings"
              :key="i"
              :title="w"
              type="warning"
              :closable="false"
              show-icon
              class="warn-item"
            />
          </div>
          <div class="ece-small ece-muted mt10">问题：{{ question }}</div>
        </template>
      </div>
    </div>

    <div class="foot-note">
      AI 为只读问询：不执行库存写操作，不伪造未配置的大模型结果；库存变更必须在“库存与补货”页人工确认。
    </div>
  </div>
</template>

<style scoped>
.mb12 { margin-bottom: 12px; }
.mt10 { margin-top: 12px; }
.two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.banner-text { font-size: 12px; line-height: 1.8; margin-top: 4px; }
.tool-list { margin: 0; padding-left: 18px; display: grid; gap: 6px; font-size: 12px; line-height: 1.7; color: #4a5f58; }
.answer-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; color: #758487; align-items: center; }
.tool-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.warn-item { margin-bottom: 6px; }
.brief-box { margin-top: 12px; }
.brief-box p { margin: 0 0 8px; font-size: 12px; }
.brief-preview {
  background: #f7faf5; border: 1px solid #e5e9e5; border-radius: 8px; padding: 10px 12px;
  max-height: 220px; overflow: auto; white-space: pre-wrap; font-size: 11.5px; line-height: 1.8;
  color: #3d524b; margin: 0 0 8px;
}
.foot-note { color: #8a9a92; font-size: 11px; line-height: 1.8; padding-top: 4px; }
</style>
