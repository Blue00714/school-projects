<script lang="ts" setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import { ArrowUp, Document, Loading, Plus, Promotion } from '@element-plus/icons-vue'
import { download, ece, streamAi, type AiAnswer, type AiEvent } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { fmt } from '@/utils/format'

defineOptions({ name: 'EceAi' })

type AnswerStage = Extract<AiEvent, { stage: 'answer' }>
interface Turn {
  id: number
  question: string
  stage: 'connect' | 'query' | 'answer' | 'writing' | 'legacy' | 'done'
  text: string
  source?: AnswerStage
  result?: AiAnswer
  error?: string
}

const suggestions = [
  '最近30天销量最高的商品是什么？',
  '近期经营情况如何？',
  '全店未来7天预计卖多少件？',
]
const toolNames: Record<string, string> = {
  dashboard_summary: '经营看板',
  product_search: '商品档案',
  product_ranking: '商品销量排行',
  orders_query: '交易订单',
  forecast_summary: '全店预测',
  inventory_lookup: '模拟库存',
  batch_info: '数据批次',
  saved_forecast: '已保存预测',
  saved_backtest: '已保存回测',
  simulated_inventory_overview: '模拟库存概况',
}
const stages = {
  connect: '正在连接问答服务',
  query: '正在查询当前批次',
  answer: '数据已查到，正在组织回答',
  writing: '正在生成回答',
  legacy: '正在等待当前实例返回回答',
}
const userStore = useUserStore()
const turns = ref<Turn[]>([])
const question = ref('')
const composer = ref<HTMLTextAreaElement>()
const scrollArea = ref<HTMLDivElement>()
const asking = ref(false)
const briefOpen = ref(false)
const briefLoading = ref(false)
const brief = ref<any>(null)
const briefError = ref('')
const batchLabel = computed(() => userStore.batch?.label || '当前数据批次')
let controller: AbortController | undefined
let turnId = 0

function followAnswer(force = false) {
  const panel = scrollArea.value
  if (!panel) return
  const isNearBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 200
  if (force || isNearBottom) void nextTick(() => panel.scrollTo({ top: panel.scrollHeight }))
}

function handleEvent(turn: Turn, event: AiEvent) {
  if (event.type === 'stage') {
    turn.stage = event.stage
    if (event.stage === 'answer') turn.source = event
  } else if (event.type === 'delta') {
    turn.stage = 'writing'
    turn.text += event.text
  } else if (event.type === 'done') {
    turn.result = event.result
    turn.text = event.result.clarification || event.result.answer || ''
    turn.stage = 'done'
  }
  followAnswer()
}

async function ask(text = question.value) {
  const value = text.trim()
  if (!value || asking.value) return
  const turn = reactive<Turn>({ id: ++turnId, question: value, stage: 'connect', text: '' })
  turns.value.push(turn)
  question.value = ''
  asking.value = true
  const request = new AbortController()
  controller = request
  followAnswer(true)
  try {
    await streamAi(value, event => handleEvent(turn, event), request.signal)
  } catch (error) {
    turn.error = request.signal.aborted ? '已停止接收回答' : error instanceof Error ? error.message : '问答请求失败'
    turn.stage = 'done'
  } finally {
    controller = undefined
    asking.value = false
    followAnswer()
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void ask()
}

function clearChat() {
  turns.value = []
  question.value = ''
  composer.value?.focus()
}

function rangeLabel(range: AiAnswer['range']) {
  return [
    range.start && (range.end && range.start !== range.end ? `${range.start} 至 ${range.end}` : range.start),
    range.product,
    range.country,
  ].filter(Boolean).join(' · ')
}

function answerLines(value: string) {
  return value.trim().split('\n').filter(line => line.trim()).map(line => {
    const title = /^#{1,6}\s+/.test(line)
    const text = line.replace(/^#{1,6}\s+/, '').replace(/^\s*[-*]\s+/, '• ')
    return { title, parts: text.split(/\*\*([^*]+)\*\*/g) }
  })
}

function preview(turn: Turn) {
  const summary = turn.source?.tool_calls.find(call => call.ok)?.summary || ''
  const first = summary.split('；')[0] || summary
  return first.length > 175 ? `${first.slice(0, 175)}…` : first
}

async function generateBrief() {
  briefOpen.value = true
  if (briefLoading.value) return
  briefLoading.value = true
  briefError.value = ''
  brief.value = null
  try {
    brief.value = await ece.aiBrief({})
  } catch (error) {
    briefError.value = error instanceof Error ? error.message : '简报生成失败'
  } finally {
    briefLoading.value = false
  }
}

function downloadBrief() {
  const saved = brief.value?.brief
  if (saved) void download(saved.url, `${saved.title || '运营简报'}.md`)
}

onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <main class="ai-workspace">
    <header class="ai-header">
      <div class="ai-title"><h1>AI 助手</h1><span>{{ batchLabel }}</span></div>
      <div class="ai-actions">
        <button type="button" @click="generateBrief"><el-icon><Document /></el-icon><span>运营简报</span></button>
        <button v-if="turns.length" type="button" :disabled="asking" @click="clearChat">
          <el-icon><Plus /></el-icon><span>新对话</span>
        </button>
      </div>
    </header>

    <div ref="scrollArea" class="ai-scroll">
      <div v-if="!turns.length" class="welcome">
        <div class="welcome-icon"><el-icon><Promotion /></el-icon></div>
        <h2>今天想查什么？</h2>
        <p>直接问销量、订单、全店预测或模拟库存。日期、商品和国家可以写在问题里。</p>
        <div class="suggestions">
          <button v-for="item in suggestions" :key="item" type="button" @click="ask(item)">{{ item }}</button>
        </div>
      </div>

      <div v-else class="conversation">
        <section v-for="turn in turns" :key="turn.id" class="turn">
          <div class="user-message">{{ turn.question }}</div>
          <div class="assistant-message">
            <div class="assistant-heading"><span class="assistant-icon"><el-icon><Promotion /></el-icon></span>运营助手</div>
            <p v-if="turn.stage !== 'done'" class="progress" role="status">
              <el-icon class="is-loading"><Loading /></el-icon>{{ stages[turn.stage] }}<span v-if="turn.source">· 取数 {{ fmt(turn.source.query_seconds, 2) }} 秒</span>
            </p>
            <p v-if="turn.source && !turn.text" class="preview"><strong>已查到</strong> {{ preview(turn) }}</p>
            <div v-if="turn.text" class="answer-text" :aria-busy="turn.stage !== 'done'">
              <p v-for="(line, index) in answerLines(turn.text)" :key="index" :class="{ 'answer-title': line.title }">
                <template v-for="(part, partIndex) in line.parts" :key="partIndex">
                  <strong v-if="partIndex % 2">{{ part }}</strong><template v-else>{{ part }}</template>
                </template>
              </p>
            </div>
            <p v-if="turn.error || turn.result?.error" class="message-error" role="alert">{{ turn.error || turn.result?.error }}</p>
            <div v-if="turn.result" class="answer-footer">
              <span v-if="turn.result.llm_used">模型根据查询结果生成</span>
              <span v-else-if="turn.result.status === 'ok'">本地查询结果</span>
              <span v-if="turn.result.stage_timings">取数 {{ fmt(turn.result.stage_timings.query_seconds, 2) }} 秒</span>
              <span v-if="turn.result.stage_timings?.model_seconds != null">模型耗时 {{ fmt(turn.result.stage_timings.model_seconds, 2) }} 秒</span>
              <span>总耗时 {{ fmt(turn.result.elapsed_seconds, 2) }} 秒</span>
            </div>
            <details v-if="turn.result || turn.source" class="sources">
              <summary>数据来源与范围</summary>
              <p>{{ (turn.result || turn.source)?.batch.label }}</p>
              <p v-if="rangeLabel((turn.result || turn.source)!.range)">{{ rangeLabel((turn.result || turn.source)!.range) }}</p>
              <p v-if="turn.result?.stage_timings?.first_text_seconds != null">模型首字 {{ fmt(turn.result.stage_timings.first_text_seconds, 2) }} 秒</p>
              <ul v-if="(turn.result || turn.source)?.tool_calls.length">
                <li v-for="(call, index) in (turn.result || turn.source)?.tool_calls" :key="index">
                  <b>{{ toolNames[call.name] || call.name }}</b><span v-if="!call.ok"> · 未取得结果</span>
                  <div>{{ call.summary }}</div>
                </li>
              </ul>
              <p v-for="warning in turn.result?.warnings" :key="warning">{{ warning }}</p>
            </details>
          </div>
        </section>
      </div>
    </div>

    <div class="composer-dock">
      <form class="composer" @submit.prevent="ask()">
        <textarea ref="composer" v-model="question" rows="1" maxlength="1000" aria-label="提问" placeholder="问问当前批次的经营数据" @keydown="onKeydown" />
        <button v-if="asking" type="button" class="send" aria-label="停止接收回答" title="停止接收回答" @click="controller?.abort()"><span class="stop-icon" /></button>
        <button v-else type="submit" class="send" :disabled="!question.trim()" aria-label="发送问题" title="发送问题"><el-icon><ArrowUp /></el-icon></button>
      </form>
      <p class="composer-note">Enter 发送 · Shift + Enter 换行 · “最近”以当前批次截止日计算</p>
    </div>

    <el-dialog v-model="briefOpen" title="运营简报" width="min(680px, 92vw)">
      <p v-if="briefLoading" role="status">正在生成当前批次的运营简报…</p>
      <p v-else-if="briefError" class="message-error">{{ briefError }}</p>
      <pre v-else-if="brief" class="brief-preview">{{ brief.markdown }}</pre>
      <template #footer>
        <el-button v-if="brief?.brief?.id" type="primary" @click="downloadBrief">下载 Markdown</el-button>
        <el-button v-if="briefError" @click="generateBrief">重试</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.ai-workspace {
  --chat-ink: #182421;
  --chat-muted: #566662;
  --chat-border: #e2e8e4;
  --chat-accent: #205e4e;
  height: max(520px, calc(100dvh - 84px));
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: #fff;
  color: var(--chat-ink);
  font-family: 'Inter', 'Microsoft YaHei UI', sans-serif;
}
.ai-header {
  min-height: 57px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 24px;
}
.ai-title, .ai-actions { display: flex; gap: 12px; align-items: center; }
.ai-title h1 { margin: 0; font-size: 16px; font-weight: 650; white-space: nowrap; }
.ai-title span { color: var(--chat-muted); font-size: 12px; }
.ai-actions button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 7px 9px;
  color: var(--chat-muted);
  background: transparent;
  white-space: nowrap;
  cursor: pointer;
  font-size: 12px;
}
.ai-actions button:hover { background: #f3f6f4; color: var(--chat-ink); }
.ai-actions button:disabled { opacity: .45; cursor: default; }
.ai-scroll { overflow-y: auto; min-width: 0; }
.welcome {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px 24px 38px;
  text-align: center;
}
.welcome-icon, .assistant-icon {
  display: inline-grid;
  place-items: center;
  color: var(--chat-accent);
  background: #e9f3ee;
}
.welcome-icon { width: 54px; height: 54px; border-radius: 18px; font-size: 24px; }
.welcome h2 { margin: 18px 0 10px; font-size: clamp(25px, 4vw, 36px); font-weight: 650; }
.welcome p { margin: 0; max-width: 440px; color: var(--chat-muted); font-size: 14px; line-height: 1.7; }
.suggestions { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 24px; }
.suggestions button {
  border: 1px solid var(--chat-border);
  background: #fff;
  border-radius: 14px;
  padding: 9px 12px;
  color: #344944;
  font-size: 13px;
  cursor: pointer;
}
.suggestions button:hover { border-color: #8cb3a1; background: #f3f8f5; }
.conversation, .composer-dock { max-width: 804px; width: 100%; margin: 0 auto; padding-inline: 24px; }
.conversation { display: grid; gap: 30px; padding-block: 18px 28px; }
.turn { min-width: 0; }
.user-message {
  width: fit-content;
  max-width: 88%;
  margin-left: auto;
  padding: 10px 16px;
  border-radius: 18px 18px 5px 18px;
  background: #edf2ef;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
}
.assistant-message { margin-top: 18px; min-width: 0; font-size: 14px; line-height: 1.75; overflow-wrap: anywhere; }
.assistant-heading { display: flex; gap: 9px; align-items: center; font-weight: 650; }
.assistant-icon { width: 30px; height: 30px; border-radius: 10px; }
.progress { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; color: var(--chat-accent); font-size: 13px; margin: 10px 0; }
.progress span { color: var(--chat-muted); }
.preview { border-left: 2px solid #9abdac; padding-left: 12px; color: var(--chat-muted); }
.preview strong { margin-right: 4px; color: var(--chat-ink); }
.answer-text p { margin: 8px 0; white-space: pre-wrap; }
.answer-text .answer-title { font-weight: 650; margin-top: 14px; }
.message-error { color: #a3362d; font-size: 13px; }
.answer-footer { margin-top: 13px; display: flex; flex-wrap: wrap; gap: 4px 13px; color: var(--chat-muted); font-size: 12px; }
.sources { margin-top: 8px; color: var(--chat-muted); font-size: 12px; }
.sources summary { width: fit-content; cursor: pointer; font-weight: 600; color: var(--chat-accent); }
.sources p, .sources ul { margin: 6px 0; }
.sources li + li { margin-top: 6px; }
.composer-dock { padding-top: 8px; padding-bottom: 12px; }
.composer {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  border: 1px solid #ccd8d0;
  border-radius: 23px;
  padding: 10px 10px 10px 16px;
  box-shadow: 0 4px 16px #162b2010;
}
.composer:focus-within { border-color: #679882; }
.composer textarea {
  flex: 1;
  min-width: 0;
  min-height: 35px;
  max-height: 132px;
  field-sizing: content;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  padding: 7px 0;
  font: inherit;
  font-size: 15px;
  line-height: 1.5;
}
.send {
  flex: 0 0 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: var(--chat-accent);
  color: #fff;
  cursor: pointer;
  font-size: 19px;
}
.send:disabled { background: #e9eeeb; color: #778782; cursor: default; }
.stop-icon { width: 11px; height: 11px; border-radius: 2px; background: currentColor; }
.composer-note { color: var(--chat-muted); margin: 8px 0 0; font-size: 11px; text-align: center; }
.brief-preview { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 52vh; overflow-y: auto; line-height: 1.65; }
@media (max-width: 640px) {
  .ai-workspace { height: max(450px, calc(100dvh - 130px)); }
  .ai-header { padding-inline: 13px; gap: 6px; }
  .ai-title { display: grid; gap: 3px; }
  .ai-title span { font-size: 10px; }
  .ai-actions { gap: 2px; }
  .ai-actions button { padding: 6px; }
  .conversation, .composer-dock { padding-inline: 13px; }
  .welcome { padding-inline: 14px; }
}
</style>
