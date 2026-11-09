<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { addDays, fmt, isoTime, newIdempotencyKey, todayISO } from '@/utils/format'

defineOptions({ name: 'EceInventory' })

const userStore = useUserStore()

const scenarios = ref<any[]>([])
const scenarioId = ref('')
const scenario = ref<any>(null)
const items = ref<any[]>([])
const replenish = reactive<{ formula: string; items: any[] }>({
  formula: 'max(0, 提前期内场景需求 + 安全库存 - 当前库存 - 有效在途量)',
  items: [],
})
const transit = ref<any[]>([])
const movements = ref<any[]>([])
const loading = ref(false)
const savingSku = ref('')
const confirmingSku = ref('')
const receivingId = ref('')
const movementResult = ref('')
const movementFailed = ref(false)
const movementSubmitting = ref(false)
const demoLoading = ref(false)

const newScenario = reactive({ name: '', note: '', topn: 5 })

const movement = reactive({
  stock_code: '',
  kind: 'in',
  qty: 10,
  idempotency_key: '',
  note: '',
})

const itemsMap = computed(() => {
  const map: Record<string, any> = {}
  items.value.forEach(item => {
    map[item.stock_code] = item
  })
  return map
})

const inventorySummary = computed(() => ({
  needsOrder: items.value.filter(item => item.replenishment_candidate > 0).length,
  suggested: items.value.reduce((sum, item) => sum + Number(item.replenishment_candidate || 0), 0),
  inTransit: transit.value.filter(item => item.status === 'in_transit').length,
}))

function stockStatus(row: any) {
  if (row.balance < row.safety_stock) return { label: '低于安全库存', type: 'danger' as const }
  if (row.replenishment_candidate > 0) return { label: '需要补货', type: 'warning' as const }
  if (row.in_transit_effective > 0) return { label: '在途覆盖', type: 'success' as const }
  return { label: '库存充足', type: 'info' as const }
}

async function loadDemo() {
  demoLoading.value = true
  try {
    const data = await ece.inventoryDemo()
    await loadScenarios(data.scenario.id)
    ElMessage.success(data.duplicate ? '已打开演示场景，保留已有操作结果' : '演示数据已载入，可以确认补货和到货入库')
  } finally {
    demoLoading.value = false
  }
}

function regenerateKey() {
  movement.idempotency_key = newIdempotencyKey('move')
}

async function loadScenarios(selectId?: string) {
  loading.value = true
  try {
    const data = await ece.scenarios()
    scenarios.value = data.scenarios || []
    const target = selectId || scenarioId.value || scenarios.value[0]?.id
    if (target && scenarios.value.some(s => s.id === target)) {
      await selectScenario(target)
    } else if (!scenarios.value.length) {
      scenarioId.value = ''
      scenario.value = null
      items.value = []
      replenish.items = []
      transit.value = []
      movements.value = []
    }
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`场景读取失败：${error.message}`)
  } finally {
    loading.value = false
  }
}

async function selectScenario(id: string) {
  if (!id) return
  scenarioId.value = id
  loading.value = true
  try {
    const [itemsData, replenishData, transitData, movementData] = await Promise.all([
      ece.items(id),
      ece.replenishment(id),
      ece.inTransit(id),
      ece.movements(id),
    ])
    scenario.value = itemsData.scenario || null
    items.value = itemsData.items || []
    replenish.formula = replenishData.formula || replenish.formula
    replenish.items = replenishData.items || []
    prepareReplenishRows()
    transit.value = transitData.items || []
    movements.value = movementData.movements || []
    if (!items.value.some(item => item.stock_code === movement.stock_code)) movement.stock_code = items.value[0]?.stock_code || ''
    if (!movement.idempotency_key) regenerateKey()
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`模拟库存读取失败：${error.message}`)
  } finally {
    loading.value = false
  }
}

async function createScenario() {
  if (!newScenario.name.trim()) {
    ElMessage.warning('请输入场景名称')
    return
  }
  const payload: Record<string, unknown> = {
    name: newScenario.name.trim(),
    note: newScenario.note.trim(),
  }
  const topn = Math.max(0, Math.min(50, Number(newScenario.topn) || 0))
  if (topn > 0) {
    const products = userStore.options.products || []
    if (!products.length) {
      ElMessage.warning('当前批次没有可用商品列表，无法自动建立商品清单')
      return
    }
    payload.stock_codes = products.slice(0, topn).map(p => p.stock_code)
  }
  try {
    const data = await ece.createScenario(payload)
    ElMessage.success(`已新建模拟场景：${data.scenario?.name || ''}`)
    newScenario.name = ''
    newScenario.note = ''
    newScenario.topn = 5
    await loadScenarios(data.scenario?.id)
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`新建场景失败：${error.message}`)
  }
}

async function saveItem(row: any) {
  savingSku.value = row.stock_code
  try {
    await ece.updateItem(scenarioId.value, row.stock_code, {
      initial_qty: Number(row.initial_qty) || 0,
      lead_time_days: Number(row.lead_time_days) || 0,
      safety_stock: Number(row.safety_stock) || 0,
      daily_demand: Number(row.daily_demand) || 0,
    })
    ElMessage.success(`已保存商品参数：${row.stock_code}`)
    await selectScenario(scenarioId.value)
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`保存商品参数失败：${error.message}`)
  } finally {
    savingSku.value = ''
  }
}

async function submitMovement() {
  if (movementSubmitting.value) return
  if (!scenarioId.value) {
    ElMessage.warning('请先选择或新建一个模拟场景')
    return
  }
  const qty = Number(movement.qty)
  if (!Number.isFinite(qty) || qty <= 0) {
    ElMessage.warning('数量必须大于 0')
    return
  }
  movementSubmitting.value = true
  movementFailed.value = false
  try {
    const data = await ece.addMovement(scenarioId.value, {
      stock_code: movement.stock_code,
      kind: movement.kind,
      qty,
      idempotency_key: movement.idempotency_key || undefined,
      note: movement.note.trim(),
    })
    const balance = data.item?.balance
    movementResult.value = data.duplicate
      ? `幂等键命中：未重复记账。商品 ${movement.stock_code} 当前余额 ${fmt(balance)}（模拟）。`
      : `${movement.kind === 'in' ? '入库' : '出库'}成功：${movement.stock_code} ${fmt(qty)} 件，当前余额 ${fmt(balance)}（模拟）。`
    ElMessage.success(movementResult.value)
    movement.note = ''
    regenerateKey()
    await selectScenario(scenarioId.value)
  } catch (error: any) {
    if (error?.status !== 401) {
      movementFailed.value = true
      movementResult.value = `提交失败：${error.message}`
      ElMessage.error(`库存流水提交失败：${error.message}`)
    }
  } finally {
    movementSubmitting.value = false
  }
}

async function confirmReplenish(row: any) {
  confirmingSku.value = row.stock_code
  try {
    await ece.confirmReplenishment(scenarioId.value, {
      stock_code: row.stock_code,
      qty: Number(row._qty) || 1,
      expected_date: row._expected,
      idempotency_key: newIdempotencyKey('replenish'),
    })
    ElMessage.success(`已登记在途补货：${row.stock_code} × ${row._qty}（模拟，未直接增加库存）`)
    await selectScenario(scenarioId.value)
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`确认补货失败：${error.message}`)
  } finally {
    confirmingSku.value = ''
  }
}

async function receiveTransit(row: any) {
  receivingId.value = row.id
  try {
    const data = await ece.receiveTransit(scenarioId.value, row.id)
    ElMessage.success(`已到货入库，余额更新为 ${fmt(data.item?.balance)}（模拟）`)
    await selectScenario(scenarioId.value)
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`到货入库失败：${error.message}`)
  } finally {
    receivingId.value = ''
  }
}

function defaultExpectedDate(stockCode: string) {
  const lead = Number(itemsMap.value[stockCode]?.lead_time_days)
  return addDays(todayISO(), Number.isFinite(lead) ? lead : 7)
}

function prepareReplenishRows() {
  replenish.items = replenish.items.map(item => ({
    ...item,
    _qty: Math.ceil(Number(item.candidate) || 0),
    _expected: defaultExpectedDate(item.stock_code),
  }))
}

onMounted(async () => {
  if (!userStore.options.products?.length) await userStore.loadOptions()
  regenerateKey()
  await loadScenarios()
  prepareReplenishRows()
})
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <div class="ece-eyebrow">SIMULATED INVENTORY</div>
        <h1>库存与补货</h1>
        <p>查看库存缺口，确认补货，在到货后登记入库。</p>
      </div>
      <div><el-tag type="warning" effect="plain">模拟场景</el-tag><el-button type="primary" :loading="demoLoading" @click="loadDemo" style="margin-left: 12px">载入演示数据</el-button></div>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="商品与销量参考来自历史交易；库存、采购和流水为模拟设定。可载入12个商品的完整场景，演练缺货补货、在途覆盖和到货入库。"
      class="mb12"
    />

    <div v-if="scenario" class="ece-kpi-grid mb12">
      <div class="ece-kpi"><div class="ece-kpi__label">场景商品</div><div class="ece-kpi__value">{{ items.length }}</div><div class="ece-kpi__foot">{{ scenario.name }}</div></div>
      <div class="ece-kpi"><div class="ece-kpi__label">需要补货</div><div class="ece-kpi__value">{{ inventorySummary.needsOrder }}</div><div class="ece-kpi__foot">个商品存在提前期缺口</div></div>
      <div class="ece-kpi"><div class="ece-kpi__label">建议补货合计</div><div class="ece-kpi__value">{{ fmt(inventorySummary.suggested) }}</div><div class="ece-kpi__foot">件 · 确认后登记采购在途</div></div>
      <div class="ece-kpi"><div class="ece-kpi__label">待到货采购</div><div class="ece-kpi__value">{{ inventorySummary.inTransit }}</div><div class="ece-kpi__foot">笔 · 到货入库后更新余额</div></div>
    </div>
    <el-empty v-if="!scenario && !loading" description="还没有库存场景，点击右上角“载入演示数据”即可开始" />

    <div v-if="scenario" class="ece-panel mb12">
      <div class="ece-panel-head">
        <div><h2>商品库存概览</h2><p>日需求以历史销量作参考，补货量为当前模拟场景的提前期缺口。</p></div>
        <el-select v-model="scenarioId" placeholder="选择场景" style="width: 250px" @change="selectScenario">
          <el-option v-for="s in scenarios" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </div>
      <p class="ece-small ece-muted">{{ scenario.note }}</p>
      <el-table :data="items" v-loading="loading" size="small">
        <el-table-column label="商品" min-width="220"><template #default="{ row }"><b>{{ row.name }}</b><div class="ece-small ece-muted">{{ row.stock_code }}</div></template></el-table-column>
        <el-table-column label="状态" width="135"><template #default="{ row }"><el-tag :type="stockStatus(row).type" size="small">{{ stockStatus(row).label }}</el-tag></template></el-table-column>
        <el-table-column label="日需求参考" width="110" align="right"><template #default="{ row }">{{ fmt(row.daily_demand) }}</template></el-table-column>
        <el-table-column label="当前库存" width="100" align="right"><template #default="{ row }">{{ fmt(row.balance) }}</template></el-table-column>
        <el-table-column label="安全库存" width="100" align="right"><template #default="{ row }">{{ fmt(row.safety_stock) }}</template></el-table-column>
        <el-table-column label="有效在途" width="100" align="right"><template #default="{ row }">{{ fmt(row.in_transit_effective) }}</template></el-table-column>
        <el-table-column label="建议补货" width="105" align="right"><template #default="{ row }"><b>{{ fmt(row.replenishment_candidate) }}</b></template></el-table-column>
      </el-table>
    </div>

    <el-collapse class="mb12">
      <el-collapse-item title="场景管理、参数编辑与出入库登记" name="manage">
    <div class="ece-cards-2">
      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>场景</h2>
            <p>选择或新建模拟场景</p>
          </div>
          <el-button :loading="loading" @click="loadScenarios()">刷新</el-button>
        </div>
        <div class="ece-inline-form" style="margin-bottom: 12px">
          <el-select v-model="scenarioId" placeholder="选择场景" style="width: 280px" @change="selectScenario">
            <el-option v-for="s in scenarios" :key="s.id" :label="`${s.name}（${s.item_count} 商品）`" :value="s.id" />
          </el-select>
        </div>
        <el-form label-position="top">
          <el-form-item label="新建场景名称">
            <el-input v-model="newScenario.name" placeholder="例如 双十一补货演练" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="newScenario.note" placeholder="可选" />
          </el-form-item>
          <el-form-item label="从当前批次建立商品清单（前 N 个热销商品，0=空场景）">
            <el-input-number v-model="newScenario.topn" :min="0" :max="50" />
          </el-form-item>
          <el-button type="primary" @click="createScenario">新建场景</el-button>
        </el-form>
        <div v-if="scenario" class="scenario-summary ece-small">
          <p><b>当前场景</b>：{{ scenario.name }}<span v-if="scenario.note"> · {{ scenario.note }}</span></p>
          <p>
            商品 {{ fmt(items.length) }} 个 · <el-tag size="small" type="warning">模拟库存</el-tag>
          </p>
        </div>
      </div>

      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>入库 / 出库</h2>
            <p>出库余额不足会被拒绝；重复提交不会重复记账</p>
          </div>
        </div>
        <el-form label-position="top">
          <el-form-item label="商品">
            <el-select v-model="movement.stock_code" filterable style="width: 100%">
              <el-option
                v-for="item in items"
                :key="item.stock_code"
                :label="`${item.stock_code} · ${item.name || ''}`"
                :value="item.stock_code"
              />
            </el-select>
          </el-form-item>
          <div class="two-col">
            <el-form-item label="类型">
              <el-select v-model="movement.kind" style="width: 100%">
                <el-option label="入库" value="in" />
                <el-option label="出库" value="out" />
              </el-select>
            </el-form-item>
            <el-form-item label="数量">
              <el-input-number v-model="movement.qty" :min="1" :step="1" style="width: 100%" />
            </el-form-item>
          </div>
          <el-form-item label="备注">
            <el-input v-model="movement.note" placeholder="可选" />
          </el-form-item>
          <el-button type="primary" :disabled="!scenarioId" :loading="movementSubmitting" @click="submitMovement">登记出入库</el-button>
        </el-form>
        <el-alert
          v-if="movementResult"
          :title="movementResult"
          :type="movementFailed ? 'error' : 'success'"
          :closable="false"
          show-icon
          style="margin-top: 12px"
        />
      </div>
    </div>

    <div class="ece-panel">
      <div class="ece-panel-head">
        <div>
          <h2>商品参数</h2>
          <p>初始库存 / 提前期 / 安全库存 / 日需求，可直接编辑并保存</p>
        </div>
        <el-tag size="small" effect="plain">{{ items.length }} 个商品</el-tag>
      </div>
      <el-table :data="items" v-loading="loading" size="small">
        <el-table-column label="库存状态" width="140">
          <template #default="{ row }"><el-tag :type="stockStatus(row).type" size="small">{{ stockStatus(row).label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="商品编码" width="110" prop="stock_code" />
        <el-table-column label="名称" min-width="200" prop="name" />
        <el-table-column label="初始库存" width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.initial_qty" :min="0" :step="1" size="small" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="提前期 / 天" width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.lead_time_days" :min="0" :max="365" :step="1" size="small" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="安全库存" width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.safety_stock" :min="0" :step="1" size="small" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="日需求" width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.daily_demand" :min="0" :step="1" size="small" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="当前余额" width="90" align="right">
          <template #default="{ row }"><b>{{ fmt(row.balance) }}</b></template>
        </el-table-column>
        <el-table-column label="有效在途" width="90" align="right">
          <template #default="{ row }">{{ fmt(row.in_transit_effective) }}</template>
        </el-table-column>
        <el-table-column label="建议补货" width="90" align="right">
          <template #default="{ row }">{{ fmt(row.replenishment_candidate) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :loading="savingSku === row.stock_code" @click="saveItem(row)">保存参数</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

      </el-collapse-item>
    </el-collapse>

    <div class="ece-panel">
      <div class="ece-panel-head">
        <div>
          <h2>补货建议（模拟）</h2>
          <p>公式：{{ replenish.formula }}</p>
        </div>
        <el-button @click="selectScenario(scenarioId)">刷新建议</el-button>
      </div>
      <el-table :data="replenish.items" size="small">
        <el-table-column label="商品编码" width="110" prop="stock_code" />
        <el-table-column label="名称" min-width="180" prop="name" />
        <el-table-column label="当前库存" width="90" align="right">
          <template #default="{ row }">{{ fmt(row.current_qty) }}</template>
        </el-table-column>
        <el-table-column label="提前期内需求" width="110" align="right">
          <template #default="{ row }">{{ fmt(row.lead_time_demand) }}</template>
        </el-table-column>
        <el-table-column label="有效在途" width="90" align="right">
          <template #default="{ row }">{{ fmt(row.effective_in_transit) }}</template>
        </el-table-column>
        <el-table-column label="安全库存" width="90" align="right">
          <template #default="{ row }">{{ fmt(row.safety_stock) }}</template>
        </el-table-column>
        <el-table-column label="建议补货量" width="100" align="right">
          <template #default="{ row }"><b>{{ fmt(row.candidate) }}</b></template>
        </el-table-column>
        <el-table-column label="确认补货（模拟）" min-width="240">
          <template #default="{ row }">
            <div class="confirm-row">
              <el-input-number v-model="row._qty" :min="1" size="small" style="width: 100px" />
              <el-date-picker v-model="row._expected" type="date" value-format="YYYY-MM-DD" size="small" style="width: 140px" />
              <el-button size="small" :disabled="row.candidate <= 0" :loading="confirmingSku === row.stock_code" @click="confirmReplenish(row)">{{ row.candidate > 0 ? '确认补货' : '无需补货' }}</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="ece-cards-2">
      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>在途列表</h2>
            <p>确认补货只登记在途，到货后点“到货入库”</p>
          </div>
          <el-button @click="selectScenario(scenarioId)">刷新</el-button>
        </div>
        <el-table :data="transit" size="small" max-height="260">
          <el-table-column label="商品" width="110" prop="stock_code" />
          <el-table-column label="数量" width="80" align="right">
            <template #default="{ row }">{{ fmt(row.qty) }}</template>
          </el-table-column>
          <el-table-column label="预计到货" width="120" prop="expected_date" />
          <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'received' ? 'success' : 'warning'" size="small">{{ row.status === 'received' ? '已入库' : '运输中' }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'in_transit'"
                size="small"
                :loading="receivingId === row.id"
                @click="receiveTransit(row)"
              >
                到货入库
              </el-button>
              <span v-else class="ece-muted">已完成</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="ece-panel">
        <div class="ece-panel-head">
          <div>
            <h2>库存流水</h2>
            <p>按时间倒序 · 最多 200 条</p>
          </div>
          <el-button @click="selectScenario(scenarioId)">刷新</el-button>
        </div>
        <el-table :data="movements" size="small" max-height="260">
          <el-table-column label="时间" width="150">
            <template #default="{ row }">{{ isoTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="商品" width="100" prop="stock_code" />
          <el-table-column label="类型" width="80">
            <template #default="{ row }">
              <el-tag :type="row.kind === 'in' ? 'success' : 'info'" size="small">
                {{ row.kind === 'in' ? '入库' : '出库' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="70" align="right">
            <template #default="{ row }">{{ fmt(row.qty) }}</template>
          </el-table-column>
          <el-table-column label="余额" width="80" align="right">
            <template #default="{ row }">{{ fmt(row.balance_after) }}</template>
          </el-table-column>
          <el-table-column label="业务备注" min-width="160">
            <template #default="{ row }">
              <div v-if="row.note" class="ece-small ece-muted">{{ row.note }}</div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="foot-note">
      计算定义：balance = 初始库存 + Σ入库 − Σ出库；lead_time_demand = 提前期 × 日需求；candidate =
      max(0, lead_time_demand + 安全库存 − balance − effective_in_transit)。
    </div>
  </div>
</template>

<style scoped>
.mb12 { margin-bottom: 12px; }
.two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.key-row { display: flex; gap: 8px; width: 100%; }
.scenario-summary { margin-top: 10px; line-height: 1.8; color: #4a5f58; }
.scenario-summary p { margin: 4px 0; }
.confirm-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.foot-note { color: #8a9a92; font-size: 11px; line-height: 1.8; padding: 2px 2px 10px; }
</style>
