<script lang="ts" setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { download, ece } from '@/apis/ece'
import { useUserStore } from '@/stores/user'
import { addDays, fmt, isoTime, money } from '@/utils/format'

defineOptions({ name: 'EceOrders' })

const userStore = useUserStore()
const route = useRoute()

const filters = ref({ start: '', end: '', product: '', country: '', q: '' })
const activeTab = ref<'orders' | 'lines'>('orders')

const orders = ref<any[]>([])
const ordersTotal = ref(0)
const ordersPage = ref(1)
const ordersLoading = ref(false)
const lines = ref<any[]>([])
const linesTotal = ref(0)
const linesPage = ref(1)
const linesLoading = ref(false)
const pageSize = 25

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<any>(null)

const productQ = ref('')
const productRows = ref<any[]>([])
const productLoading = ref(false)

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

async function loadOrders() {
  ordersLoading.value = true
  try {
    const data = await ece.orders({ ...filters.value, page: ordersPage.value, page_size: pageSize })
    orders.value = data.rows || []
    ordersTotal.value = data.total || 0
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`订单读取失败：${error.message}`)
  } finally {
    ordersLoading.value = false
  }
}

async function loadLines() {
  linesLoading.value = true
  try {
    const data = await ece.transactions({ ...filters.value, page: linesPage.value, page_size: pageSize })
    lines.value = data.rows || []
    linesTotal.value = data.total || 0
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`交易明细读取失败：${error.message}`)
  } finally {
    linesLoading.value = false
  }
}

function search() {
  ordersPage.value = 1
  linesPage.value = 1
  if (activeTab.value === 'orders') loadOrders()
  else loadLines()
}

function resetFilters() {
  filters.value.product = ''
  filters.value.country = ''
  filters.value.q = ''
  applyDefaults()
  search()
}

function switchTab(tab: string | number) {
  activeTab.value = tab as 'orders' | 'lines'
  if (tab === 'orders') loadOrders()
  else loadLines()
}

async function openDetail(row: any) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    // 明细接口与列表共享 start/end/product/country 口径，必须带上当前筛选
    detail.value = await ece.orderDetail(row.order_key, {
      start: filters.value.start,
      end: filters.value.end,
      product: filters.value.product,
      country: filters.value.country,
    })
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`订单明细读取失败：${error.message}`)
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function searchProducts() {
  productLoading.value = true
  try {
    productRows.value = await ece.products(productQ.value.trim())
  } catch (error: any) {
    if (error?.status !== 401) ElMessage.error(`商品搜索失败：${error.message}`)
  } finally {
    productLoading.value = false
  }
}

function useProduct(sku: string) {
  filters.value.product = sku
  search()
}

function exportOrders() {
  download(`/api/export/orders?${qs(filters.value)}`, 'orders.csv')
}
function exportLines() {
  download(`/api/export/transactions?${qs(filters.value)}`, 'transactions.csv')
}
function exportTrend() {
  download(`/api/export/trend?${qs(filters.value)}`, 'sales-trend.csv')
}

onMounted(async () => {
  if (!userStore.options.countries?.length) await userStore.loadOptions()
  applyDefaults()
  if (typeof route.query.product === 'string' && route.query.product) {
    filters.value.product = route.query.product
  }
  await loadOrders()
})

watch(
  () => route.query.product,
  value => {
    if (typeof value === 'string' && value) {
      filters.value.product = value
      search()
    }
  },
)
</script>

<template>
  <div class="ece-page">
    <div class="ece-page-head">
      <div>
        <div class="ece-eyebrow">PRODUCTS &amp; ORDERS</div>
        <h1>商品与订单</h1>
        <p>商品搜索、订单列表与明细、交易流水，支持 CSV 导出；订单按 invoice 去重。</p>
      </div>
      <div class="ece-actions">
        <el-button @click="exportOrders">↓ 订单 CSV</el-button>
        <el-button @click="exportLines">↓ 交易明细 CSV</el-button>
        <el-button @click="exportTrend">↓ 趋势 CSV</el-button>
      </div>
    </div>

    <div class="ece-panel">
      <el-form class="ece-filter" label-position="top" @submit.prevent>
        <el-form-item label="开始日期">
          <el-date-picker v-model="filters.start" type="date" value-format="YYYY-MM-DD" style="width: 145px" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="filters.end" type="date" value-format="YYYY-MM-DD" style="width: 145px" />
        </el-form-item>
        <el-form-item label="商品编码">
          <el-select v-model="filters.product" filterable clearable placeholder="全部商品" style="width: 200px">
            <el-option
              v-for="p in userStore.options.products"
              :key="p.stock_code"
              :label="`${p.stock_code} · ${p.name || ''}`"
              :value="p.stock_code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="国家">
          <el-select v-model="filters.country" clearable placeholder="全部国家" style="width: 150px">
            <el-option v-for="c in userStore.options.countries" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="订单号 / 关键词">
          <el-input v-model="filters.q" placeholder="订单号前缀或商品描述" style="width: 190px" />
        </el-form-item>
        <el-form-item label=" ">
          <el-button type="primary" @click="search">查询</el-button>
        </el-form-item>
        <el-form-item label=" ">
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="ece-panel">
      <div class="ece-panel-head">
        <div>
          <h2>商品搜索</h2>
          <p>按编码或名称模糊查询；点击“用作筛选”把商品带入订单/明细查询</p>
        </div>
        <el-tag size="small" effect="plain">≤ 50</el-tag>
      </div>
      <div class="ece-inline-form">
        <el-input
          v-model="productQ"
          placeholder="输入商品编码或名称，如 85123 / CHRISTMAS"
          style="width: 320px"
          @keyup.enter="searchProducts"
        />
        <el-button :loading="productLoading" @click="searchProducts">搜索商品</el-button>
      </div>
      <el-table :data="productRows" size="small" style="margin-top: 12px">
        <el-table-column label="商品编码" width="130" prop="stock_code" />
        <el-table-column label="名称" prop="name" min-width="260" />
        <el-table-column label="销量 / 件" width="120" align="right">
          <template #default="{ row }">{{ fmt(row.quantity) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="useProduct(row.stock_code)">用作筛选</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="ece-panel">
      <el-tabs v-model="activeTab" @tab-change="switchTab">
        <el-tab-pane label="订单列表" name="orders">
          <div class="ece-toolbar">
            <span class="ece-muted ece-small">按日倒序 · 订单数按 invoice 去重</span>
            <el-tag size="small" effect="plain">共 {{ fmt(ordersTotal) }} 单</el-tag>
          </div>
          <el-table
            :data="orders"
            v-loading="ordersLoading"
            size="small"
            @row-click="openDetail"
            highlight-current-row
          >
            <el-table-column label="订单号" width="110" prop="order_key" />
            <el-table-column label="日期" width="112" prop="date" />
            <el-table-column label="首次 / 末次时间" min-width="170">
              <template #default="{ row }">
                <div class="ece-small">{{ row.first_time || '—' }}</div>
                <div class="ece-small ece-muted">{{ row.last_time || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="客户" width="90" prop="customer_id" />
            <el-table-column label="国家" min-width="120" prop="country" />
            <el-table-column label="行数" width="70" align="right">
              <template #default="{ row }">{{ fmt(row.lines) }}</template>
            </el-table-column>
            <el-table-column label="销量" width="90" align="right">
              <template #default="{ row }">{{ fmt(row.quantity) }}</template>
            </el-table-column>
            <el-table-column label="交易额 / £" width="120" align="right">
              <template #default="{ row }">{{ money(row.revenue) }}</template>
            </el-table-column>
            <el-table-column label="商品编码" min-width="180">
              <template #default="{ row }">
                <span class="ece-mono">{{ (row.product_codes || []).join(', ') }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="ece-pagination">
            <el-pagination
              v-model:current-page="ordersPage"
              :page-size="pageSize"
              :total="ordersTotal"
              layout="prev, pager, next, total"
              @current-change="loadOrders"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="交易明细" name="lines">
          <div class="ece-toolbar">
            <span class="ece-muted ece-small">正向交易行 · 分页 {{ pageSize }} 行</span>
            <el-tag size="small" effect="plain">共 {{ fmt(linesTotal) }} 行</el-tag>
          </div>
          <el-table :data="lines" v-loading="linesLoading" size="small">
            <el-table-column label="开单时间 / 订单号" min-width="180">
              <template #default="{ row }">
                <div class="ece-small">{{ isoTime(row.invoice_time) }}</div>
                <div class="ece-mono">{{ row.invoice }}</div>
              </template>
            </el-table-column>
            <el-table-column label="商品" min-width="240">
              <template #default="{ row }">
                <div>{{ row.description || '—' }}</div>
                <div class="ece-mono ece-muted">{{ row.stock_code }}</div>
              </template>
            </el-table-column>
            <el-table-column label="数量" width="80" align="right">
              <template #default="{ row }">{{ fmt(row.quantity) }}</template>
            </el-table-column>
            <el-table-column label="单价 / £" width="100" align="right">
              <template #default="{ row }">{{ fmt(row.unit_price, 2) }}</template>
            </el-table-column>
            <el-table-column label="金额 / £" width="110" align="right">
              <template #default="{ row }">
                {{ row.revenue === null || row.revenue === undefined ? '—' : money(row.revenue) }}
              </template>
            </el-table-column>
            <el-table-column label="国家" width="120" prop="country" />
            <el-table-column label="来源表 / 行" min-width="150">
              <template #default="{ row }">{{ row.sheet || '—' }} / {{ row.source_row }}</template>
            </el-table-column>
          </el-table>
          <div class="ece-pagination">
            <el-pagination
              v-model:current-page="linesPage"
              :page-size="pageSize"
              :total="linesTotal"
              layout="prev, pager, next, total"
              @current-change="loadLines"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="detailVisible" title="订单明细" width="900px">
      <div v-loading="detailLoading">
        <template v-if="detail?.order">
          <el-descriptions :column="3" size="small" border>
            <el-descriptions-item label="订单号">{{ detail.order.order_key }}</el-descriptions-item>
            <el-descriptions-item label="日期">{{ detail.order.date }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ detail.order.customer_id || '—' }}</el-descriptions-item>
            <el-descriptions-item label="国家">{{ detail.order.country || '—' }}</el-descriptions-item>
            <el-descriptions-item label="行数">{{ fmt(detail.order.lines) }}</el-descriptions-item>
            <el-descriptions-item label="销量 / 交易额">
              {{ fmt(detail.order.quantity) }} 件 / {{ money(detail.order.revenue) }}
            </el-descriptions-item>
          </el-descriptions>
          <el-table :data="detail.rows || []" size="small" style="margin-top: 14px">
            <el-table-column label="商品编码" width="110" prop="stock_code" />
            <el-table-column label="描述" min-width="220" prop="description" />
            <el-table-column label="数量" width="80" align="right">
              <template #default="{ row }">{{ fmt(row.quantity) }}</template>
            </el-table-column>
            <el-table-column label="单价 / £" width="100" align="right">
              <template #default="{ row }">{{ fmt(row.unit_price, 2) }}</template>
            </el-table-column>
            <el-table-column label="金额 / £" width="110" align="right">
              <template #default="{ row }">
                {{ row.revenue === null || row.revenue === undefined ? '—' : money(row.revenue) }}
              </template>
            </el-table-column>
            <el-table-column label="开单时间" width="160">
              <template #default="{ row }">{{ isoTime(row.invoice_time) }}</template>
            </el-table-column>
          </el-table>
        </template>
        <div v-else-if="!detailLoading" class="ece-empty">未读取到明细</div>
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ece-pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
