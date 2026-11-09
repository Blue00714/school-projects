/**
 * 本系统真实接口层（课程原型）。
 *
 * 与 mall-admin-web 模板自带的 {code,data} 包装接口不同，本系统 /api/* 直接返回裸 JSON：
 * - 成功：HTTP 2xx + 业务 JSON（无 code/data 包装）；
 * - 失败：HTTP 4xx/5xx + {"error": "人可读中文", "code": "bad_request|unauthorized|..."}；
 * - 会话：Cookie `ece_session`（HttpOnly），因此所有请求 withCredentials=true、同源相对路径。
 * 401 统一清空本地用户态并回到登录页；其它错误统一 ElMessage 展示后端 error。
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'

export interface EceUser {
  id: number
  username: string
  display_name: string
  role: 'admin' | 'operator'
  active: boolean
  must_change_password: boolean
}

export interface BatchInfo {
  id: string
  label: string
  kind: string
  status?: string
  is_active?: boolean
  manifest?: Record<string, unknown>
}

export interface EceOptions {
  manifest: Record<string, any>
  countries: string[]
  products: { stock_code: string; name: string; quantity: number }[]
  methods: Record<string, string>
  batch?: BatchInfo
  forecast?: { scope?: string; horizon?: number }
}

export const http = axios.create({
  baseURL: '', // 同源相对路径，不写死端口
  timeout: 120000,
  withCredentials: true,
})

http.interceptors.response.use(
  response => response.data,
  async error => {
    const status: number | undefined = error.response?.status
    const data = error.response?.data
    const message: string =
      (data && (data.error || (typeof data === 'string' ? data : ''))) ||
      error.message ||
      '请求失败'
    if (status === 401) {
      try {
        const { useUserStore } = await import('@/stores/user')
        const store = useUserStore()
        const wasLogged = !!store.user
        store.user = null
        // 首次进入的会话探测（/api/auth/me 未登录）属于正常情况，不弹错误提示
        if (wasLogged) ElMessage.error('登录已失效，请重新登录')
      } catch {
        /* store 未就绪时忽略 */
      }
      if (!window.location.hash.startsWith('#/login')) {
        window.location.hash = '#/login'
      }
    } else {
      ElMessage.error(message)
    }
    const err = new Error(message) as Error & { status?: number; data?: unknown }
    err.status = status
    err.data = data
    return Promise.reject(err)
  },
)

export function cleanParams(params: Record<string, unknown> = {}) {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined),
  )
}

const get = (url: string, params?: Record<string, unknown>) =>
  http.get(url, { params: params ? cleanParams(params) : undefined }) as Promise<any>
const post = (url: string, body?: unknown) => http.post(url, body) as Promise<any>
const put = (url: string, body?: unknown) => http.put(url, body) as Promise<any>
const patch = (url: string, body?: unknown) => http.patch(url, body) as Promise<any>

/** 鉴权 / 基线信息 */
export const ece = {
  health: () => get('/api/health'),
  login: (username: string, password: string) => post('/api/auth/login', { username, password }),
  logout: () => post('/api/auth/logout'),
  me: () => get('/api/auth/me'),
  options: () => get('/api/options'),
  settings: () => get('/api/settings'),

  /** 看板 / 订单 / 明细 / 商品 */
  dashboard: (params: Record<string, unknown>) => get('/api/dashboard', params),
  products: (q: string) => get('/api/products', { q }),
  orders: (params: Record<string, unknown>) => get('/api/orders', params),
  orderDetail: (orderKey: string, params: Record<string, unknown>) =>
    get(`/api/orders/${encodeURIComponent(orderKey)}`, params),
  transactions: (params: Record<string, unknown>) => get('/api/transactions', params),

  /** 数据导入与批次 */
  importSubmit: (file: File, label: string) => {
    const form = new FormData()
    form.append('file', file, file.name)
    if (label) form.append('label', label)
    return http.post('/api/imports', form) as Promise<any>
  },
  importJob: (jobId: string) => get(`/api/imports/jobs/${encodeURIComponent(jobId)}`),
  batches: () => get('/api/imports/batches'),
  activateBatch: (batchId: string) =>
    post(`/api/imports/batches/${encodeURIComponent(batchId)}/activate`),
  templateUrl: '/api/imports/template.csv',

  /** 预测与回测 */
  forecast: (body: Record<string, unknown>) => post('/api/forecast', body),
  backtest: (body: Record<string, unknown>) => post('/api/backtest', body),
  latestForecast: () => get('/api/forecast/latest'),
  job: (jobId: string) => get(`/api/jobs/${encodeURIComponent(jobId)}`),

  /** 模拟库存 */
  scenarios: () => get('/api/inventory/scenarios'),
  inventoryDemo: () => post('/api/inventory/demo'),
  createScenario: (body: Record<string, unknown>) => post('/api/inventory/scenarios', body),
  items: (sid: string) => get(`/api/inventory/scenarios/${encodeURIComponent(sid)}/items`),
  updateItem: (sid: string, sku: string, body: Record<string, unknown>) =>
    put(`/api/inventory/scenarios/${encodeURIComponent(sid)}/items/${encodeURIComponent(sku)}`, body),
  movements: (sid: string) =>
    get(`/api/inventory/scenarios/${encodeURIComponent(sid)}/movements`),
  addMovement: (sid: string, body: Record<string, unknown>) =>
    post(`/api/inventory/scenarios/${encodeURIComponent(sid)}/movements`, body),
  replenishment: (sid: string) =>
    get(`/api/inventory/scenarios/${encodeURIComponent(sid)}/replenishment`),
  confirmReplenishment: (sid: string, body: Record<string, unknown>) =>
    post(`/api/inventory/scenarios/${encodeURIComponent(sid)}/replenishment/confirm`, body),
  inTransit: (sid: string) =>
    get(`/api/inventory/scenarios/${encodeURIComponent(sid)}/in-transit`),
  receiveTransit: (sid: string, transitId: string) =>
    post(
      `/api/inventory/scenarios/${encodeURIComponent(sid)}/in-transit/${encodeURIComponent(transitId)}/receive`,
    ),

  /** AI 助手 */
  aiStatus: () => get('/api/ai/status'),
  aiTools: () => get('/api/ai/tools'),
  aiAsk: (body: Record<string, unknown>) => post('/api/ai/ask', body),
  aiBrief: (body: Record<string, unknown>) => post('/api/ai/brief', body),

  /** 系统管理 */
  users: () => get('/api/users'),
  createUser: (body: Record<string, unknown>) => post('/api/users', body),
  patchUser: (id: number, body: Record<string, unknown>) =>
    patch(`/api/users/${encodeURIComponent(String(id))}`, body),
}

/** 下载 CSV / Markdown：同源 Cookie；错误时解析后端 {error}。 */
export async function downloadFile(path: string, fallbackName: string) {
  try {
    const res = await axios.get(path, { withCredentials: true, responseType: 'blob' })
    const cd: string = res.headers['content-disposition'] || ''
    const match = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd)
    const name = match?.[1] ? decodeURIComponent(match[1].replace(/"/g, '').trim()) : fallbackName
    const url = URL.createObjectURL(res.data)
    const link = document.createElement('a')
    link.href = url
    link.download = name
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 4000)
    ElMessage.success(`已开始下载：${name}`)
  } catch (error: any) {
    let message = '下载失败'
    const blob = error?.response?.data
    if (blob instanceof Blob) {
      try {
        const parsed = JSON.parse(await blob.text())
        message = parsed.error || message
      } catch {
        /* 非 JSON */
      }
    } else if (error?.response?.data?.error) {
      message = error.response.data.error
    } else if (error?.message) {
      message = error.message
    }
    ElMessage.error(message)
    throw error
  }
}

export function download(path: string, name: string) {
  return downloadFile(path, name)
}
