/** 通用格式化与日期工具（与旧版前端口径一致） */

export function fmt(value: unknown, digits = 0): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return String(value)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

export function money(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return `£ ${fmt(value, 2)}`
}

export function pct(value: unknown, digits = 1): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(digits)}%`
}

export function num(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

export function isoTime(value: unknown): string {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 19)
}

export function todayISO(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00`)
  if (Number.isNaN(d.getTime())) return iso
  d.setDate(d.getDate() + days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function daysBetween(start: string, end: string): number {
  const a = new Date(`${start}T12:00:00`)
  const b = new Date(`${end}T12:00:00`)
  return Math.round((b.getTime() - a.getTime()) / 86400000)
}

/** 幂等键：前端随机生成，重复提交同一键时后端不重复记账。 */
export function newIdempotencyKey(prefix = 'ui'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function deltaText(value: unknown): string {
  if (value === null || value === undefined) return '前期范围不足或基数为 0'
  const n = Number(value)
  const arrow = n >= 0 ? '↑' : '↓'
  return `${arrow} ${pct(Math.abs(n))} 对比前一等长区间`
}
