'use strict';
/* 智能电商运营管理系统 · 课程原型
 * 原生 JS 单页应用：登录 + 看板/商品与订单/数据导入/预测与回测/模拟库存/AI 助手/系统管理
 * 所有请求同源相对路径（/api/...），无框架、无 CDN、无构建。 */

/* ============================== 基础工具 ============================== */
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const fmt = (v, d = 0) => v === null || v === undefined || v === '' || Number.isNaN(Number(v))
  ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: d, minimumFractionDigits: d });
const money = (v) => v === null || v === undefined ? '—' : '£ ' + fmt(v, 2);
const pct = (v, d = 1) => v === null || v === undefined ? '—' : (Number(v) * 100).toFixed(d) + '%';
const num = (v) => v === null || v === undefined ? '0' : String(v);
const isNum = (v) => v !== null && v !== undefined && Number.isFinite(Number(v));

class ApiError extends Error {
  constructor(message, status, code) { super(message); this.name = 'ApiError'; this.status = status || 0; this.code = code || ''; }
}

const ROLE_LABEL = { admin: 'admin · 管理员', operator: 'operator · 运营' };
const PAGE_TITLE = {
  dashboard: '看板', orders: '商品与订单', imports: '数据导入', forecast: '预测与回测',
  inventory: '模拟库存', ai: 'AI 助手', admin: '系统管理'
};
const TERMINAL = { succeeded: 1, failed: 1, duplicate: 1 };

const state = {
  user: null, health: null, options: null, manifest: {}, batch: null, aiStatus: null, aiTools: [],
  loaded: {}, page: 'dashboard', metric: 'quantity',
  dashboard: null,
  ordersPage: 1, linesPage: 1, orderFilters: {}, ordersTotal: 0, linesTotal: 0, lastOrderKey: null,
  importJobId: null, importPolling: false, batches: [],
  forecastJobId: null, backtestJobId: null,
  scenarioId: null, scenario: null, items: [], itemsMap: {}, replenishment: [], replenishMap: {},
  busy: {}
};

/* ============================== HTTP ============================== */
function apiUrl(path, params) {
  if (!params) return path;
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v).trim() !== '') q.set(k, String(v).trim());
  });
  const s = q.toString();
  return s ? path + (path.includes('?') ? '&' : '?') + s : path;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', credentials: 'same-origin', headers: Object.assign({}, opts.headers || {}) };
  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) init.body = opts.body;
    else { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  }
  let res;
  try { res = await fetch(path, init); }
  catch (e) { throw new ApiError('网络请求失败，请确认本地服务正在运行', 0, 'network'); }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
  if (res.status === 401) {
    if (state.user && !opts.quiet401) handleUnauthorized();
    throw new ApiError((data && data.error) || '未登录或登录已失效', 401, (data && data.code) || 'unauthorized');
  }
  if (!res.ok) {
    const msg = (data && data.error) || ('请求失败（HTTP ' + res.status + '）');
    throw new ApiError(msg, res.status, (data && data.code) || '');
  }
  return data;
}

async function downloadFile(path, fallbackName) {
  let res;
  try { res = await fetch(path, { credentials: 'same-origin' }); }
  catch (e) { throw new ApiError('下载失败：无法连接服务', 0); }
  if (res.status === 401) { handleUnauthorized(); throw new ApiError('登录已失效，请重新登录', 401); }
  if (!res.ok) {
    let msg = '下载失败（HTTP ' + res.status + '）';
    try { const d = await res.json(); if (d && d.error) msg = d.error; } catch (e) { /* 非 JSON */ }
    throw new ApiError(msg, res.status);
  }
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd);
  const name = m ? decodeURIComponent(m[1].replace(/"/g, '').trim()) : fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  toast('已开始下载：' + name);
}

/* ============================== 全局提示 ============================== */
let toastTimer = null;
function toast(message, kind) {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast' + (kind === 'error' ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3600);
}
function setError(message) {
  const el = $('global-error');
  el.textContent = message || '';
  el.classList.toggle('hidden', !message);
}
function setInfo(message) {
  const el = $('global-info');
  el.textContent = message || '';
  el.classList.toggle('hidden', !message);
}
function clearMessages() { setError(''); setInfo(''); }
function setTask(el, text, cls) {
  if (!el) return;
  el.textContent = text;
  el.className = 'task-state' + (cls ? ' ' + cls : '');
  el.classList.remove('hidden');
}
function busyButton(btn, busy) {
  if (!btn) return;
  if (busy) { btn.dataset.orig = btn.textContent; btn.disabled = true; btn.textContent = '处理中…'; }
  else { btn.disabled = false; if (btn.dataset.orig) { btn.textContent = btn.dataset.orig; delete btn.dataset.orig; } }
}

/* ============================== 日期工具 ============================== */
function todayISO() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function addDays(iso, n) {
  const d = new Date(iso + 'T12:00:00');
  if (Number.isNaN(d.getTime())) return iso;
  d.setDate(d.getDate() + n);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function daysBetween(a, b) {
  const da = new Date(a + 'T12:00:00'), db = new Date(b + 'T12:00:00');
  return Math.round((db - da) / 86400000);
}
function isoTime(v) { return v ? String(v).replace('T', ' ').slice(0, 19) : '—'; }

/* ============================== 登录 / 会话 ============================== */
function showLogin(message, kind) {
  state.user = null;
  $('app-view').classList.add('hidden');
  $('login-view').classList.remove('hidden');
  $('login-password').value = '';
  const box = $('login-error');
  if (message) {
    box.textContent = message;
    box.className = 'alert ' + (kind === 'info' ? 'info' : 'error');
    box.classList.remove('hidden');
  } else box.classList.add('hidden');
  setTimeout(() => { try { $('login-username').focus(); } catch (e) { /* ignore */ } }, 40);
}
function handleUnauthorized() {
  if (!state.user) return;
  state.user = null;
  state.loaded = {};
  showLogin('登录已失效（401），请重新登录。');
}
async function checkHealth() {
  try {
    const h = await api('/api/health', { quiet401: true });
    state.health = h;
    const bits = [];
    bits.push(h.service || 'ecommerce-ops-fastapi');
    bits.push('v' + (h.version || '—'));
    bits.push(h.data_ready ? '数据就绪' : '数据未就绪');
    bits.push(h.ai_configured ? 'AI：已配置' : 'AI：未配置（本地规则工具层）');
    if (h.batch && h.batch.label) bits.push('批次：' + h.batch.label);
    $('login-health').textContent = '服务正常 · ' + bits.join(' · ');
    if (h.batch) state.batch = state.batch || h.batch;
    $('footer-version').textContent = (h.version || 'prototype');
  } catch (e) {
    $('login-health').textContent = e.status === 404
      ? '服务已启动，但 /api/health 未就绪（后端仍在开发）'
      : '服务状态未知：' + e.message;
  }
}
async function doLogin(event) {
  event.preventDefault();
  const username = $('login-username').value.trim();
  const password = $('login-password').value;
  if (!username || !password) {
    $('login-error').textContent = '请输入账号和密码';
    $('login-error').classList.remove('hidden');
    return;
  }
  const btn = $('login-submit');
  busyButton(btn, true);
  $('login-error').classList.add('hidden');
  try {
    const data = await api('/api/auth/login', { method: 'POST', body: { username, password }, quiet401: true });
    if (!data || !data.user) throw new ApiError('登录响应缺少用户信息', 0);
    state.user = data.user;
    await enterApp();
  } catch (e) {
    $('login-error').textContent = e.message || '登录失败';
    $('login-error').classList.remove('hidden');
  } finally {
    busyButton(btn, false);
  }
}
async function logout() {
  try { await api('/api/auth/logout', { method: 'POST', quiet401: true }); } catch (e) { /* 忽略退出错误 */ }
  state.user = null;
  state.loaded = {};
  state.aiStatus = null;
  showLogin('已退出登录。', 'info');
  $('login-health').textContent = '已退出登录';
}

/* ============================== 应用外壳 ============================== */
function renderUser() {
  const u = state.user || {};
  $('user-name').textContent = u.display_name || u.username || '—';
  const roleEl = $('user-role');
  roleEl.textContent = u.role === 'admin' ? 'admin · 管理员' : (u.role === 'operator' ? 'operator · 运营' : (u.role || '—'));
  roleEl.className = 'role-badge' + (u.role === 'operator' ? ' operator' : '');
  document.querySelectorAll('.nav-admin').forEach((el) => el.classList.toggle('hidden', u.role !== 'admin'));
  if (u.role !== 'admin' && state.page === 'admin') go('dashboard');
}
function renderBatchHeader() {
  const batch = state.batch || {};
  const m = state.manifest || {};
  const label = batch.label || (state.health && state.health.batch && state.health.batch.label) || '批次未知';
  const kind = batch.kind === 'upload' ? '上传批次' : (batch.kind === 'original' ? '原始批次' : '');
  $('batch-label').textContent = label + (kind ? ' · ' + kind : '');
  const coverage = (m.date_min && m.date_max) ? m.date_min + ' ~ ' + m.date_max : '覆盖范围未知';
  $('batch-coverage').textContent = coverage;
  $('policy-coverage').textContent = label + ' · ' + coverage;
  $('batch-chip').title = '当前数据批次：' + label + '；覆盖：' + coverage;
}
function renderCountries() {
  const countries = (state.options && state.options.countries) || [];
  const options = '<option value="">全部国家</option>' + countries.map((c) => '<option value="' + esc(c) + '">' + esc(c) + '</option>').join('');
  ['dash-country', 'ord-country', 'ai-country'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    const old = el.value;
    el.innerHTML = options;
    if (old && countries.includes(old)) el.value = old;
  });
}
function renderProductDatalist() {
  const products = (state.options && state.options.products) || [];
  $('product-options').innerHTML = products.map((p) =>
    '<option value="' + esc(p.stock_code) + '">' + esc(p.name || '') + '</option>').join('');
}
function renderModelSelect() {
  const methods = (state.options && state.options.methods) || {
    blend_rule: '周季节与LightGBM组合＋零销量日规则', lgbm: 'LightGBM直接7步',
    holt_winters: '固定参数周季节Holt-Winters', seasonal_week4: '同星期几近4周均值', naive_week: '上周同日'
  };
  const sel = $('forecast-model');
  const old = sel.value;
  sel.innerHTML = Object.entries(methods).map(([k, v]) =>
    '<option value="' + esc(k) + '">' + esc(v) + '</option>').join('');
  const preferred = old && methods[old] ? old : (methods['blend_rule'] ? 'blend_rule' : Object.keys(methods)[0]);
  if (preferred) sel.value = preferred;
  updateModelDescription();
}
function updateModelDescription() {
  const methods = (state.options && state.options.methods) || {};
  const key = $('forecast-model').value;
  $('model-description').textContent = (methods[key] || key || '—') + ' · 固定参数模型，仅验证全店日记录销量。';
}
function applyDefaultDates(force) {
  const m = state.manifest || {};
  const end = m.date_max || '';
  const earliest = m.date_min || '';
  const start = end ? addDays(end, -29) : '';
  const startClamped = (start && earliest && start < earliest) ? earliest : start;
  const outOfCoverage = (v) => !v || (earliest && v < earliest) || (end && v > end);
  const assign = (id, value) => {
    const el = $(id);
    if (!el || !value) return;
    if (force || outOfCoverage(el.value)) el.value = value;
  };
  [['dash-start', startClamped], ['dash-end', end], ['ord-start', startClamped], ['ord-end', end]]
    .forEach(([id, v]) => assign(id, v));
  // 预测起点/回测区间必须严格落在当前批次覆盖范围内，切换批次后自动收敛。
  const origin = outOfCoverage($('forecast-origin') ? $('forecast-origin').value : '') ? end : $('forecast-origin').value;
  if ($('forecast-origin')) $('forecast-origin').value = origin || end;
  let btEnd = end;
  if ($('backtest-end')) {
    const cur = $('backtest-end').value;
    btEnd = (force || outOfCoverage(cur)) ? end : cur;
    $('backtest-end').value = btEnd;
  }
  if ($('backtest-start')) {
    const bs = btEnd ? addDays(btEnd, -6) : '';
    const cur = $('backtest-start').value;
    const invalid = force || outOfCoverage(cur) || (btEnd && cur > btEnd);
    $('backtest-start').value = invalid ? ((bs && earliest && bs < earliest) ? earliest : bs) : cur;
  }
  if ($('dash-range')) {
    $('dash-range').textContent = (startClamped && end) ? (startClamped + ' 至 ' + end + ' · 默认最近 30 天（按数据覆盖）') : '日期范围未知';
  }
}
async function loadOptions() {
  try {
    const data = await api('/api/options');
    state.options = data;
    state.manifest = (data && data.manifest) || {};
    state.batch = (data && data.batch) || state.batch;
  } catch (e) {
    state.options = state.options || { manifest: {}, countries: [], products: [], methods: {} };
    state.manifest = state.manifest || (state.options.manifest || {});
    setError('读取筛选选项失败：' + e.message + '（看板与筛选可能不可用）');
  }
  renderBatchHeader();
  renderCountries();
  renderProductDatalist();
  renderModelSelect();
  applyDefaultDates(true);
}
async function enterApp() {
  $('login-view').classList.add('hidden');
  $('app-view').classList.remove('hidden');
  renderUser();
  renderBatchHeader();
  bindAppEvents();
  await loadOptions();
  const hash = (location.hash || '').replace('#', '');
  let target = PAGE_TITLE[hash] ? hash : 'dashboard';
  if (target === 'admin' && (!state.user || state.user.role !== 'admin')) target = 'dashboard';
  go(target, true);
}
function go(page, force) {
  if (!PAGE_TITLE[page]) page = 'dashboard';
  if (page === 'admin' && (!state.user || state.user.role !== 'admin')) {
    setError('系统管理仅管理员可见。');
    page = 'dashboard';
  }
  state.page = page;
  document.querySelectorAll('.page').forEach((el) => el.classList.toggle('hidden', el.id !== 'page-' + page));
  document.querySelectorAll('.nav').forEach((el) => el.classList.toggle('active', el.dataset.page === page));
  $('breadcrumb').textContent = PAGE_TITLE[page];
  if (location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
  window.scrollTo({ top: 0, behavior: 'auto' });
  clearMessages();
  if (force || !state.loaded[page]) {
    state.loaded[page] = true;
    loadPage(page);
  }
}
function loadPage(page) {
  const loaders = {
    dashboard: loadDashboard, orders: loadOrdersPage, imports: loadImportsPage,
    forecast: loadForecastPage, inventory: loadInventoryPage, ai: loadAiPage, admin: loadAdminPage
  };
  const fn = loaders[page];
  if (fn) fn().catch((e) => { if (e.status !== 401) setError(e.message); });
}
function refreshAfterBatchSwitch() {
  state.loaded = {};
  renderBatchHeader();
  const page = state.page;
  state.loaded[page] = true;
  loadPage(page);
}

/* ============================== 事件绑定 ============================== */
let eventsBound = false;
function bindAppEvents() {
  if (eventsBound) return;
  eventsBound = true;

  document.querySelectorAll('.nav').forEach((btn) => btn.addEventListener('click', () => go(btn.dataset.page)));
  $('logout').addEventListener('click', logout);

  /* ---- 看板 ---- */
  $('dashboard-filter').addEventListener('submit', (e) => { e.preventDefault(); loadDashboard(); });
  $('dashboard-refresh').addEventListener('click', () => loadDashboard());
  $('dash-reset').addEventListener('click', () => {
    $('dash-product').value = ''; $('dash-country').value = '';
    applyDefaultDates(); loadDashboard();
  });
  document.querySelectorAll('#metric-tabs [data-metric]').forEach((btn) => btn.addEventListener('click', () => {
    state.metric = btn.dataset.metric;
    document.querySelectorAll('#metric-tabs [data-metric]').forEach((b) => b.classList.toggle('active', b === btn));
    if (state.dashboard) renderTrend();
  }));
  document.querySelectorAll('[data-days]').forEach((btn) => btn.addEventListener('click', () => {
    const end = state.manifest.date_max || $('dash-end').value;
    const startRaw = end ? addDays(end, -Number(btn.dataset.days) + 1) : '';
    const earliest = state.manifest.date_min || '';
    $('dash-end').value = end;
    $('dash-start').value = (startRaw && earliest && startRaw < earliest) ? earliest : startRaw;
    document.querySelectorAll('[data-days]').forEach((b) => b.classList.toggle('active', b === btn));
    loadDashboard();
  }));
  $('dashboard-export-trend').addEventListener('click', () => exportWithFilters('/api/export/trend', 'sales-trend.csv', dashboardFilters()));

  /* ---- 商品与订单 ---- */
  $('orders-filter').addEventListener('submit', (e) => { e.preventDefault(); state.ordersPage = 1; state.linesPage = 1; loadOrdersPage(true); });
  $('ord-reset').addEventListener('click', () => {
    $('ord-product').value = ''; $('ord-country').value = ''; $('ord-q').value = '';
    $('ord-start').value = $('dash-start').value; $('ord-end').value = $('dash-end').value;
    state.ordersPage = 1; state.linesPage = 1; loadOrdersPage(true);
  });
  $('order-subtabs').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-sub]');
    if (!btn) return;
    switchOrderSub(btn.dataset.sub);
  });
  $('orders-prev').addEventListener('click', () => { if (state.ordersPage > 1) { state.ordersPage--; loadOrders(); } });
  $('orders-next').addEventListener('click', () => {
    if (state.ordersPage * 25 < state.ordersTotal) { state.ordersPage++; loadOrders(); }
  });
  $('lines-prev').addEventListener('click', () => { if (state.linesPage > 1) { state.linesPage--; loadLines(); } });
  $('lines-next').addEventListener('click', () => {
    if (state.linesPage * 25 < state.linesTotal) { state.linesPage++; loadLines(); }
  });
  $('order-detail-close').addEventListener('click', () => $('order-detail-panel').classList.add('hidden'));
  $('product-search-form').addEventListener('submit', searchProducts);
  $('export-orders').addEventListener('click', () => exportWithFilters('/api/export/orders', 'orders.csv', orderFilters()));
  $('export-transactions').addEventListener('click', () => exportWithFilters('/api/export/transactions', 'transactions.csv', orderFilters()));
  $('export-trend-2').addEventListener('click', () => exportWithFilters('/api/export/trend', 'sales-trend.csv', orderFilters()));

  /* ---- 数据导入 ---- */
  $('import-form').addEventListener('submit', submitImport);
  $('batches-refresh').addEventListener('click', () => loadBatches().catch((e) => setError(e.message)));

  /* ---- 预测与回测 ---- */
  $('forecast-model').addEventListener('change', updateModelDescription);
  $('run-forecast').addEventListener('click', runForecast);
  $('run-backtest').addEventListener('click', runBacktest);
  $('forecast-export').addEventListener('click', () => {
    if (!state.forecastJobId) { setError('请先生成预测任务'); return; }
    downloadFile('/api/jobs/' + encodeURIComponent(state.forecastJobId) + '/export', 'forecast.csv')
      .catch((e) => setError(e.message));
  });
  $('backtest-export').addEventListener('click', () => {
    if (!state.backtestJobId) { setError('请先运行回测任务'); return; }
    downloadFile('/api/jobs/' + encodeURIComponent(state.backtestJobId) + '/export', 'backtest.csv')
      .catch((e) => setError(e.message));
  });

  /* ---- 模拟库存 ---- */
  $('inventory-refresh').addEventListener('click', () => loadScenarios(true).catch((e) => setError(e.message)));
  $('scenario-load').addEventListener('click', () => {
    if ($('scenario-select').value) selectScenario($('scenario-select').value).catch((e) => setError(e.message));
  });
  $('scenario-form').addEventListener('submit', createScenario);
  $('movement-form').addEventListener('submit', submitMovement);
  $('movement-idem-new').addEventListener('click', () => { $('movement-idem').value = newIdempotencyKey('move'); });
  $('items-body').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-save]');
    if (btn) saveItem(btn.dataset.save, btn);
  });
  $('replenish-body').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-confirm]');
    if (btn) confirmReplenishment(btn.dataset.confirm, btn);
  });
  $('replenish-refresh').addEventListener('click', () => loadReplenishment().catch((e) => setError(e.message)));
  $('in-transit-body').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-receive]');
    if (btn) receiveInTransit(btn.dataset.receive, btn);
  });
  $('in-transit-refresh').addEventListener('click', () => loadInTransit().catch((e) => setError(e.message)));
  $('movements-refresh').addEventListener('click', () => loadMovements().catch((e) => setError(e.message)));

  /* ---- AI ---- */
  $('ai-status-refresh').addEventListener('click', () => loadAiStatus(true));
  $('ai-form').addEventListener('submit', askAi);
  $('ai-brief').addEventListener('click', createBrief);

  /* ---- 系统管理 ---- */
  $('users-refresh').addEventListener('click', () => loadUsers().catch((e) => setError(e.message)));
  $('user-form').addEventListener('submit', createUser);
  $('users-body').addEventListener('click', onUserAction);
  $('users-body').addEventListener('change', onUserRoleChange);
}

/* ============================== 看板 ============================== */
function dashboardFilters() {
  return {
    start: $('dash-start').value, end: $('dash-end').value,
    product: $('dash-product').value.trim(), country: $('dash-country').value
  };
}
function deltaText(v) {
  if (v === null || v === undefined) return '前期范围不足或基数为 0，不计算增长率';
  return '<span class="delta ' + (v < 0 ? 'negative' : '') + '">' + (v >= 0 ? '↑' : '↓') + ' ' + pct(Math.abs(v)) + '</span> 对比前一等长区间';
}
function kpiCard(label, value, foot) {
  return '<div class="kpi"><div class="kpi-label">' + esc(label) + '</div><div class="kpi-value">' +
    esc(value) + '</div><div class="kpi-foot">' + (foot || '') + '</div></div>';
}
async function loadDashboard() {
  const btn = $('dash-apply');
  busyButton(btn, true);
  clearMessages();
  try {
    const d = await api(apiUrl('/api/dashboard', dashboardFilters()));
    state.dashboard = d;
    state.batch = d.batch || state.batch;
    renderDashboard(d);
  } catch (e) {
    if (e.status !== 401) setError('看板读取失败：' + e.message);
    $('dashboard-kpis').innerHTML = '<div class="kpi skeleton">看板数据暂不可用</div>';
  } finally {
    busyButton(btn, false);
  }
}
function renderDashboard(d) {
  const t = d.totals || {};
  state.batch = d.batch || state.batch;
  renderBatchHeader();
  $('dashboard-kpis').innerHTML =
    kpiCard('销量 / 件', fmt(t.quantity), deltaText(d.changes && d.changes.quantity)) +
    kpiCard('销售额 / GBP', money(t.revenue), deltaText(d.changes && d.changes.revenue)) +
    kpiCard('订单数', fmt(t.orders), deltaText(d.changes && d.changes.orders)) +
    kpiCard('平均订单额 / GBP', money(t.average_order_value), '覆盖 ' + fmt(t.products) + ' 个商品 · ' + fmt(t.lines) + ' 条正向交易行');
  const range = d.filters ? (d.filters.start + ' 至 ' + d.filters.end) : '—';
  $('dash-range').textContent = range + ' · ' + fmt(t.lines) + ' 条正向交易明细';
  renderWarnings('dashboard-warnings', d.warnings);
  renderTrend();
  renderCountriesBars(d.countries || []);
  renderRanking(d.ranking || []);
  renderQuality(d.quality || {});
  renderDashboardPolicy(d);
  $('dashboard-footnote').textContent =
    '正向交易口径：数量大于 0、非取消，排除跨表副本；销售额不等于利润。金额覆盖 ' +
    fmt(t.amount_rows) + ' / ' + fmt(t.lines) + ' 行，保留零单价行。所选范围有 ' +
    fmt((d.quality || {}).cancelled_rows) + ' 条取消记录另列，不混入正向销售。无记录日按 0 显示，obs=0。';
}
function renderWarnings(id, warnings) {
  const el = $(id);
  if (!el) return;
  const list = Array.isArray(warnings) ? warnings : [];
  el.innerHTML = list.map((w) => '<div class="warning-item">' + esc(w) + '</div>').join('');
}
function renderTrend() {
  if (!state.dashboard) return;
  const metric = state.metric;
  const label = { quantity: '销量 / 件', revenue: '销售额 / £', orders: '订单数' }[metric];
  drawLineChart($('sales-chart'), state.dashboard.trend || [], [
    { key: metric, label: label, color: '#167967', area: true, digits: metric === 'revenue' ? 2 : 0 }
  ]);
  $('trend-note').textContent = label + ' · 灰色区间表示数据覆盖外或无记录（按 0）';
}
function renderCountriesBars(countries) {
  const el = $('country-bars');
  if (!countries.length) { el.innerHTML = '<div class="empty-cell">暂无国家分布数据</div>'; return; }
  const max = Math.max(1, ...countries.map((c) => Number(c.quantity) || 0));
  el.innerHTML = countries.map((c, i) =>
    '<div class="country-item"><span class="country-name" title="' + esc(c.country || '未记录') + '">' +
    esc(c.country || '未记录') + '</span><div class="bar-track"><div class="bar-fill" id="bar-' + i +
    '" style="width:' + (Number(c.quantity) / max * 100) + '%"></div></div><span class="num">' + fmt(c.quantity) + '</span></div>'
  ).join('');
}
function renderRanking(ranking) {
  const body = $('ranking-body');
  if (!ranking.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty-cell">此范围暂无符合口径的商品记录</td></tr>';
    return;
  }
  body.innerHTML = ranking.map((r, i) =>
    '<tr><td><span class="rank ' + (i < 3 ? 'top' : '') + '">' + String(i + 1).padStart(2, '0') + '</span></td>' +
    '<td><button class="product-link" data-sku="' + esc(r.stock_code) + '">' + esc(r.name || '未记录商品名称') +
    '<span class="sku">' + esc(r.stock_code) + '</span></button></td>' +
    '<td class="num">' + fmt(r.quantity) + '</td><td class="num">' + fmt(r.revenue, 2) + '</td>' +
    '<td class="num">' + fmt(r.orders) + '</td></tr>'
  ).join('');
  body.querySelectorAll('[data-sku]').forEach((btn) => btn.addEventListener('click', () => {
    $('ord-product').value = btn.dataset.sku;
    state.ordersPage = 1; state.linesPage = 1;
    go('orders', true);
    switchOrderSub('lines');
  }));
  $('ranking-count').textContent = 'TOP ' + ranking.length;
}
function renderQuality(q) {
  const rows = [
    ['保留行数', q.retained_rows], ['取消行', q.cancelled_rows], ['非正数量行', q.nonpositive_rows],
    ['重复候选行', q.duplicate_candidates], ['无效单价行', q.invalid_price_rows]
  ];
  $('quality-list').innerHTML = rows.map(([k, v]) =>
    '<dt>' + k + '</dt><dd>' + fmt(v) + '</dd>').join('');
}
function renderDashboardPolicy(d) {
  const m = state.manifest || {};
  const batch = d.batch || state.batch || {};
  const coverage = d.coverage ? (d.coverage.start + ' ~ ' + d.coverage.end) : ((m.date_min || '—') + ' ~ ' + (m.date_max || '—'));
  $('dashboard-policy').innerHTML =
    '<p><b>当前批次</b>：' + esc(batch.label || '—') + '（' + esc(batch.kind || '—') + '）</p>' +
    '<p><b>数据覆盖</b>：' + esc(coverage) + '</p>' +
    '<p><b>口径版本</b>：' + esc(d.policy_version || m.policy_version || '—') + '</p>' +
    '<p><b>金额口径</b>：GBP 交易金额（transaction amount），<b>不等于利润</b>；无单价行不计入金额但保留销量。</p>' +
    '<p><b>零记录日</b>：无记录日按 0 补入连续日序列，仅表示没有符合口径的交易，不代表停业或需求为 0。</p>' +
    '<p><b>本页边界</b>：不含成本、退款利润、库存与流量数据；预测与补货为实验/模拟，不构成经营收益。</p>';
}

/* ---- SVG 折线图 ---- */
function drawLineChart(container, rows, series) {
  if (!container) return;
  const W = 920, H = 270, L = 62, R = 22, T = 16, B = 34;
  if (!rows || !rows.length) { container.innerHTML = '<div class="empty-cell">暂无可展示记录</div>'; return; }
  const values = [];
  rows.forEach((r) => series.forEach((s) => { if (isNum(r[s.key])) values.push(Number(r[s.key])); }));
  let max = values.length ? Math.max(...values) : 1;
  if (!Number.isFinite(max) || max <= 0) max = 1;
  max *= 1.12;
  const x = (i) => rows.length === 1 ? W / 2 : L + (W - L - R) * i / (rows.length - 1);
  const y = (v) => T + (H - T - B) * (1 - (Number(v) || 0) / max);
  const short = (v) => v >= 1000000 ? (v / 1000000).toFixed(1) + 'm' : v >= 1000 ? (v / 1000).toFixed(v < 10000 ? 1 : 0) + 'k' : Math.round(v).toString();
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="按日期展示的折线图">';
  for (let i = 0; i < 5; i++) {
    const v = max * i / 4, yy = y(v);
    svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + yy.toFixed(1) + '" y2="' + yy.toFixed(1) +
      '" stroke="#edf1e8" stroke-dasharray="3 5" /><text x="' + (L - 10) + '" y="' + (yy + 4).toFixed(1) +
      '" text-anchor="end" fill="#9aa895" font-size="11">' + short(v) + '</text>';
  }
  const tickCount = Math.min(rows.length, 6);
  const tickIdx = [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round(i * (rows.length - 1) / Math.max(1, tickCount - 1))))];
  tickIdx.forEach((i) => {
    const label = String(rows[i].date || '').slice(5);
    svg += '<text x="' + x(i).toFixed(1) + '" y="' + (H - 10) + '" text-anchor="middle" fill="#9aa895" font-size="11">' + esc(label) + '</text>';
  });
  series.forEach((s, si) => {
    const segments = []; let seg = [];
    rows.forEach((r, i) => {
      if (isNum(r[s.key])) seg.push([x(i), y(r[s.key])]);
      else if (seg.length) { segments.push(seg); seg = []; }
    });
    if (seg.length) segments.push(seg);
    segments.forEach((points) => {
      const path = points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
      if (s.area && si === 0) {
        svg += '<path d="' + path + ' L' + points[points.length - 1][0].toFixed(1) + ',' + y(0).toFixed(1) +
          ' L' + points[0][0].toFixed(1) + ',' + y(0).toFixed(1) + ' Z" fill="#edf4e9" />';
      }
      svg += '<path d="' + path + '" stroke="' + s.color + '" stroke-width="2.3" fill="none"' +
        (s.dashed ? ' stroke-dasharray="6 4"' : '') + ' />';
      if (rows.length <= 12) {
        points.forEach((p) => { svg += '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3.4" fill="' + s.color + '" stroke="#fff" stroke-width="1.5" />'; });
      }
    });
  });
  svg += '</svg>';
  container.innerHTML = svg;
  const tip = $('tooltip');
  container.onpointermove = (event) => {
    const rect = container.getBoundingClientRect();
    if (!rect.width) return;
    const viewX = (event.clientX - rect.left) / rect.width * W;
    let i = 0;
    if (rows.length > 1) i = Math.round((viewX - L) / (W - L - R) * (rows.length - 1));
    i = Math.max(0, Math.min(rows.length - 1, i));
    const r = rows[i];
    const lines = [String(r.date || '')];
    series.forEach((s) => lines.push(s.label + '：' + (isNum(r[s.key]) ? fmt(r[s.key], s.digits || 0) : '—')));
    if (r.observed === false) lines.push('（无符合口径的记录，按 0 显示）');
    tip.textContent = lines.join('\n');
    tip.classList.remove('hidden');
    tip.style.left = Math.min(event.clientX + 14, window.innerWidth - 250) + 'px';
    tip.style.top = Math.max(8, Math.min(event.clientY + 12, window.innerHeight - 160)) + 'px';
  };
  container.onpointerleave = () => tip.classList.add('hidden');
}

/* ============================== 商品与订单 ============================== */
function orderFilters() {
  return {
    start: $('ord-start').value, end: $('ord-end').value, product: $('ord-product').value.trim(),
    country: $('ord-country').value, q: $('ord-q').value.trim()
  };
}
async function loadOrdersPage(force) {
  if (!state.options) await loadOptions();
  renderDefaultOrderScope();
  if (force) { switchOrderSub('orders'); }
  loadOrders();
}
function renderDefaultOrderScope() {
  $('orders-scope').textContent = '按日倒序 · 订单数按 invoice 去重 · 查询条件与筛选栏一致';
}
function switchOrderSub(sub) {
  document.querySelectorAll('#order-subtabs [data-sub]').forEach((b) => b.classList.toggle('active', b.dataset.sub === sub));
  $('sub-orders').classList.toggle('hidden', sub !== 'orders');
  $('sub-lines').classList.toggle('hidden', sub !== 'lines');
  if (sub === 'orders') loadOrders();
  else loadLines();
}
async function loadOrders() {
  const body = $('orders-body');
  body.innerHTML = '<tr><td colspan="9" class="empty-cell">加载中…</td></tr>';
  $('orders-error').classList.add('hidden');
  try {
    const params = Object.assign(orderFilters(), { page: state.ordersPage, page_size: 25 });
    const d = await api(apiUrl('/api/orders', params));
    state.ordersTotal = d.total || 0;
    const rows = d.rows || [];
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-cell">当前条件下没有订单记录</td></tr>';
    } else {
      body.innerHTML = rows.map((r) =>
        '<tr class="clickable" data-order="' + esc(r.order_key) + '"><td class="mono">' + esc(r.order_key) +
        '</td><td>' + esc(r.date) + '</td><td class="tiny">' + esc(r.first_time || '—') + '<br>' + esc(r.last_time || '—') +
        '</td><td>' + esc(r.customer_id === null || r.customer_id === undefined ? '—' : r.customer_id) + '</td><td>' + esc(r.country || '—') +
        '</td><td class="num">' + fmt(r.lines) + '</td><td class="num">' + fmt(r.quantity) + '</td><td class="num">' + money(r.revenue) +
        '</td><td class="tiny mono">' + esc((r.product_codes || []).join(', ')) + '</td></tr>'
      ).join('');
      body.querySelectorAll('[data-order]').forEach((tr) => tr.addEventListener('click', () => loadOrderDetail(tr.dataset.order)));
    }
    $('orders-total').textContent = '共 ' + fmt(state.ordersTotal) + ' 单';
    const pages = Math.max(1, Math.ceil(state.ordersTotal / 25));
    $('orders-page-label').textContent = '第 ' + state.ordersPage + ' / ' + pages + ' 页';
    $('orders-prev').disabled = state.ordersPage <= 1;
    $('orders-next').disabled = state.ordersPage >= pages;
  } catch (e) {
    body.innerHTML = '<tr><td colspan="9" class="empty-cell">订单读取失败</td></tr>';
    $('orders-error').textContent = '订单读取失败：' + e.message;
    $('orders-error').classList.remove('hidden');
  }
}
async function loadOrderDetail(orderKey) {
  const panel = $('order-detail-panel');
  panel.classList.remove('hidden');
  $('order-detail-title').textContent = '订单明细 · ' + orderKey;
  $('order-detail-subtitle').textContent = '加载中…';
  $('order-detail-body').innerHTML = '<tr><td colspan="7" class="empty-cell">加载中…</td></tr>';
  try {
    // 明细接口与列表共享 start/end/product/country 口径，必须带上当前筛选，否则默认窗口外订单会 404。
    const f = orderFilters();
    const d = await api(apiUrl('/api/orders/' + encodeURIComponent(orderKey), {
      start: f.start, end: f.end, product: f.product, country: f.country
    }));
    const order = d.order || {};
    state.lastOrderKey = orderKey;
    $('order-detail-title').textContent = '订单明细 · ' + (order.order_key || orderKey);
    $('order-detail-subtitle').textContent = [order.date, order.first_time + ' ~ ' + order.last_time,
      order.country, '客户 ' + (order.customer_id || '—'), '行数 ' + order.lines, '销量 ' + fmt(order.quantity), '金额 ' + money(order.revenue)].join(' · ');
    const rows = d.rows || [];
    $('order-detail-body').innerHTML = rows.length ? rows.map((r) =>
      '<tr><td class="mono">' + esc(r.stock_code) + '</td><td>' + esc(r.description || '—') +
      '</td><td class="num">' + fmt(r.quantity) + '</td><td class="num">' + fmt(r.unit_price, 2) +
      '</td><td class="num">' + (r.revenue === null || r.revenue === undefined ? '—' : money(r.revenue)) +
      '</td><td class="tiny">' + esc(isoTime(r.invoice_time)) + '</td><td class="tiny">' +
      esc(r.sheet || '—') + ' / ' + esc(r.source_row) + '</td></tr>').join('') :
      '<tr><td colspan="7" class="empty-cell">该订单没有符合口径的明细行</td></tr>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) {
    $('order-detail-subtitle').textContent = '';
    $('order-detail-body').innerHTML = '<tr><td colspan="7" class="empty-cell">' + esc(e.message) + '</td></tr>';
  }
}
async function loadLines() {
  const body = $('lines-body');
  body.innerHTML = '<tr><td colspan="7" class="empty-cell">加载中…</td></tr>';
  try {
    const params = Object.assign(orderFilters(), { page: state.linesPage, page_size: 25 });
    const d = await api(apiUrl('/api/transactions', params));
    state.linesTotal = d.total || 0;
    const rows = d.rows || [];
    body.innerHTML = rows.length ? rows.map((r) =>
      '<tr><td class="tiny">' + esc(isoTime(r.invoice_time)) + '<br><span class="mono">' + esc(r.invoice) +
      '</span></td><td>' + esc(r.description || '—') + '<span class="sku block tiny mono">' + esc(r.stock_code) +
      '</span></td><td class="num">' + fmt(r.quantity) + '</td><td class="num">' + fmt(r.unit_price, 2) +
      '</td><td class="num">' + (r.revenue === null || r.revenue === undefined ? '—' : money(r.revenue)) +
      '</td><td>' + esc(r.country || '—') + '</td><td class="tiny">' + esc(r.sheet || '—') + ' / ' + esc(r.source_row) +
      '</td></tr>').join('') :
      '<tr><td colspan="7" class="empty-cell">当前条件下没有交易明细</td></tr>';
    $('lines-total').textContent = '共 ' + fmt(state.linesTotal) + ' 行';
    const pages = Math.max(1, Math.ceil(state.linesTotal / 25));
    $('lines-page-label').textContent = '第 ' + state.linesPage + ' / ' + pages + ' 页';
    $('lines-prev').disabled = state.linesPage <= 1;
    $('lines-next').disabled = state.linesPage >= pages;
  } catch (e) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">交易明细读取失败</td></tr>';
    setError('交易明细读取失败：' + e.message);
  }
}
async function searchProducts(event) {
  event.preventDefault();
  const q = $('product-search-q').value.trim();
  const body = $('product-search-body');
  body.innerHTML = '<tr><td colspan="4" class="empty-cell">搜索中…</td></tr>';
  try {
    const rows = await api(apiUrl('/api/products', { q }));
    if (!Array.isArray(rows) || !rows.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty-cell">没有匹配的商品</td></tr>';
      return;
    }
    body.innerHTML = rows.map((r) =>
      '<tr><td class="mono">' + esc(r.stock_code) + '</td><td>' + esc(r.name || '—') +
      '</td><td class="num">' + fmt(r.quantity) + '</td><td class="num"><button class="button tiny secondary" data-use="' +
      esc(r.stock_code) + '">用作筛选</button></td></tr>').join('');
    body.querySelectorAll('[data-use]').forEach((btn) => btn.addEventListener('click', () => {
      $('ord-product').value = btn.dataset.use;
      state.ordersPage = 1; state.linesPage = 1;
      switchOrderSub('orders');
    }));
  } catch (e) {
    body.innerHTML = '<tr><td colspan="4" class="empty-cell">' + esc(e.message) + '</td></tr>';
  }
}
function exportWithFilters(path, filename, filters) {
  downloadFile(apiUrl(path, filters), filename).catch((e) => setError('导出失败：' + e.message));
}

/* ============================== 数据导入 ============================== */
async function loadImportsPage() {
  await loadBatches();
  renderImportEmpty();
}
function renderImportEmpty() {
  if (!state.importJobId && !state.importPolling) {
    $('import-job-detail').innerHTML = '<div class="empty-cell">尚未提交导入任务</div>';
  }
}
async function submitImport(event) {
  event.preventDefault();
  const fileInput = $('import-file');
  const file = fileInput.files && fileInput.files[0];
  if (!file) { setError('请选择要导入的 .xlsx / .xls / .csv 文件'); return; }
  const form = new FormData();
  form.append('file', file, file.name);
  const label = $('import-label').value.trim();
  if (label) form.append('label', label);
  const btn = $('import-submit');
  busyButton(btn, true);
  setTask($('import-progress'), '正在上传 ' + file.name + ' …', 'busy');
  clearMessages();
  try {
    const data = await api('/api/imports', { method: 'POST', body: form });
    if (!data || !data.job_id) throw new ApiError('导入提交响应缺少 job_id', 0);
    state.importJobId = data.job_id;
    pollImportJob(data.job_id);
  } catch (e) {
    setTask($('import-progress'), '导入提交失败：' + e.message, 'fail');
    setError('导入提交失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
function pollImportJob(jobId) {
  if (state.importPolling) return;
  state.importPolling = true;
  let tries = 0;
  const finish = () => { state.importPolling = false; };
  const tick = async () => {
    if (!state.user) { finish(); return; }
    tries++;
    if (tries > 300) {
      finish();
      setTask($('import-progress'), '轮询超时，请稍后点击“刷新批次”查看结果。', 'fail');
      return;
    }
    try {
      const d = await api('/api/imports/jobs/' + encodeURIComponent(jobId));
      const job = d.job || d;
      setTask($('import-progress'), '导入任务 ' + jobId + '：' + (job.status || '未知') +
        (job.file_name ? ' · ' + job.file_name : '') + (job.error ? ' · ' + job.error : ''), 'busy');
      renderImportJob(d);
      if (TERMINAL[job.status]) {
        finish();
        const cls = job.status === 'failed' ? 'fail' : 'ok';
        const label = { succeeded: '导入成功，批次已生成（未自动切换）', duplicate: '重复文件：已存在同 SHA-256 批次', failed: '导入失败' }[job.status] || job.status;
        setTask($('import-progress'), label + (job.error ? '：' + job.error : ''), cls);
        await loadBatches();
        return;
      }
      setTimeout(tick, 1200);
    } catch (e) {
      finish();
      setTask($('import-progress'), '读取导入任务失败：' + e.message, 'fail');
    }
  };
  setTimeout(tick, 700);
}
function renderImportJob(d) {
  const job = d.job || d;
  const report = d.report || {};
  const issues = d.issues || [];
  const batch = d.batch || null;
  const statusLabel = { queued: '排队中', running: '处理中', succeeded: '成功', failed: '失败', duplicate: '重复文件' }[job.status] || job.status;
  const badgeCls = job.status === 'failed' ? 'fail' : (job.status === 'succeeded' ? 'ok' : 'neutral');
  const reportRows = [
    ['原始行数', report.raw_rows], ['保留行数', report.retained_rows], ['正向销售行', report.positive_sale_rows],
    ['跨表副本已排除', report.cross_sheet_copies_excluded], ['表内重复候选保留', report.within_sheet_duplicate_candidates_retained],
    ['取消行', report.cancelled_rows], ['非正数量行', report.nonpositive_quantity_rows], ['非法日期', report.invalid_dates],
    ['数量非法行', report.quantity_invalid_rows], ['无有效单价行', report.sale_rows_without_valid_price],
    ['零单价销售行', report.zero_price_sale_rows], ['日期范围', (report.date_min || '—') + ' ~ ' + (report.date_max || '—')],
    ['口径版本', report.policy_version], ['耗时', report.elapsed_seconds !== undefined ? fmt(report.elapsed_seconds, 2) + ' 秒' : '—']
  ].filter(([, v]) => v !== undefined && v !== null && v !== '');
  let html = '<div class="stack small">';
  html += '<div><span class="badge ' + badgeCls + '">' + esc(statusLabel) + '</span> <span class="mono">' + esc(job.id || '') +
    '</span></div><div class="muted">文件：' + esc(job.file_name || '—') + ' · 创建：' + esc(isoTime(job.created_at)) +
    (job.finished_at ? ' · 完成：' + esc(isoTime(job.finished_at)) : '') + '</div>';
  if (job.error) html += '<div class="alert error">' + esc(job.error) + '</div>';
  if (reportRows.length) {
    html += '<dl class="def-list">' + reportRows.map(([k, v]) => '<dt>' + k + '</dt><dd>' + esc(v) + '</dd>').join('') + '</dl>';
  }
  if (issues.length) {
    const shown = issues.slice(0, 100);
    html += '<div class="panel-head" style="margin:14px 0 8px"><h3>质量问题（' + issues.length + ' 条' +
      (issues.length > 100 ? '，显示前 100' : '') + '）</h3></div><div class="table-scroll short"><table><thead><tr><th>级别</th><th class="num">行</th><th>字段</th><th>说明</th></tr></thead><tbody>' +
      shown.map((it) => '<tr><td>' + esc(it.level || '') + '</td><td class="num">' + esc(it.row === null || it.row === undefined ? '—' : it.row) +
        '</td><td>' + esc(it.field || '—') + '</td><td>' + esc(it.message || '') + '</td></tr>').join('') + '</tbody></table></div>';
  }
  if (batch) {
    html += '<div class="alert info" style="margin-top:12px"><b>批次</b>：' + esc(batch.label || batch.id) + '（' + esc(batch.id) +
      '）' + (batch.is_active ? ' · 已是当前批次' : ' · 尚未切换') + '</div>';
    if (!batch.is_active && job.status === 'succeeded') {
      html += '<button class="button primary" type="button" id="import-activate">切换为当前批次</button>';
    }
  }
  html += '</div>';
  $('import-job-detail').innerHTML = html;
  const act = $('import-activate');
  if (act && batch) act.addEventListener('click', () => activateBatch(batch.id, act));
}
async function loadBatches() {
  const body = $('batches-body');
  try {
    const d = await api('/api/imports/batches');
    const batches = d.batches || [];
    state.batches = batches;
    if (!batches.length) { body.innerHTML = '<tr><td colspan="7" class="empty-cell">暂无批次</td></tr>'; return; }
    body.innerHTML = batches.map((b) => {
      const m = b.manifest || {};
      const coverage = (m.date_min && m.date_max) ? (m.date_min + ' ~ ' + m.date_max) : '—';
      return '<tr><td class="mono">' + esc(b.id) + '</td><td>' + esc(b.label || '—') + '</td><td>' + esc(b.kind || '—') +
        '</td><td class="tiny">' + esc(coverage) + '</td><td><span class="badge ' + (b.status === 'ready' ? 'ok' : 'neutral') + '">' +
        esc(b.status || '—') + '</span></td><td>' + (b.is_active ? '<span class="badge ok">当前</span>' : '—') +
        '</td><td>' + (b.is_active ? '' : '<button class="button tiny secondary" data-activate="' + esc(b.id) + '">切换</button>') +
        '</td></tr>';
    }).join('');
    body.querySelectorAll('[data-activate]').forEach((btn) => btn.addEventListener('click', () => activateBatch(btn.dataset.activate, btn)));
  } catch (e) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">批次列表读取失败：' + esc(e.message) + '</td></tr>';
  }
}
async function activateBatch(batchId, btn) {
  if (!batchId) return;
  busyButton(btn, true);
  try {
    await api('/api/imports/batches/' + encodeURIComponent(batchId) + '/activate', { method: 'POST' });
    toast('已切换批次：' + batchId + '，正在刷新数据…');
    await loadOptions();
    await loadBatches();
    // 让“最近导入任务”卡片里的批次状态同步为“已是当前批次”，避免切换后仍显示未切换。
    if (state.importJobId) {
      try { renderImportJob(await api('/api/imports/jobs/' + encodeURIComponent(state.importJobId))); } catch (e) { /* 旧任务不可读时忽略 */ }
    }
    refreshAfterBatchSwitch();
  } catch (e) {
    setError('切换批次失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}

/* ============================== 预测与回测 ============================== */
async function loadForecastPage() {
  if (!state.options) await loadOptions();
  applyDefaultDates();
  $('forecast-empty').classList.remove('hidden');
  $('forecast-content').classList.add('hidden');
  $('backtest-content').classList.add('hidden');
}
function validateOrigin(origin) {
  const m = state.manifest || {};
  if (!origin) return '请选择预测起点';
  if (m.date_min && m.date_max) {
    if (origin < m.date_min || origin > m.date_max) return '预测起点必须在数据覆盖范围 ' + m.date_min + ' ~ ' + m.date_max + ' 内';
  }
  return '';
}
async function runForecast() {
  const origin = $('forecast-origin').value;
  const model = $('forecast-model').value;
  const err = validateOrigin(origin);
  if (err) { setError(err); setTask($('forecast-task'), err, 'fail'); return; }
  const btn = $('run-forecast');
  busyButton(btn, true);
  clearMessages();
  setTask($('forecast-task'), '正在提交预测任务…', 'busy');
  try {
    const data = await api('/api/forecast', { method: 'POST', body: { origin, model } });
    const jobId = data.job_id || data.job_id;
    if (!jobId) throw new ApiError('预测提交响应缺少 job_id', 0);
    state.forecastJobId = jobId;
    setTask($('forecast-task'), '任务已排队：' + jobId + ' · 批次 ' + (data.batch_id || '—'), 'busy');
    pollForecastJob(jobId, 'forecast');
  } catch (e) {
    setTask($('forecast-task'), '提交失败：' + e.message, 'fail');
    setError('预测提交失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
function pollForecastJob(jobId, kind) {
  const taskEl = kind === 'forecast' ? $('forecast-task') : $('backtest-task');
  let tries = 0;
  const tick = async () => {
    if (!state.user) return;
    tries++;
    if (tries > 400) { setTask(taskEl, '轮询超时，请稍后重试。', 'fail'); return; }
    try {
      const d = await api('/api/jobs/' + encodeURIComponent(jobId));
      const job = d.job || d;
      const elapsed = job.elapsed_seconds !== undefined && job.elapsed_seconds !== null ? ' · ' + fmt(job.elapsed_seconds, 1) + 's' : '';
      setTask(taskEl, '任务 ' + jobId + '：' + (job.status || '未知') + elapsed, 'busy');
      if (job.status === 'succeeded') {
        setTask(taskEl, '任务成功：' + jobId + elapsed, 'ok');
        let result = job.result;
        if (typeof result === 'string') { try { result = JSON.parse(result); } catch (e) { result = null; } }
        if (!result) { setError('任务成功但结果为空'); return; }
        if (kind === 'forecast') renderForecastResult(job, result);
        else renderBacktestResult(job, result);
        return;
      }
      if (job.status === 'failed') {
        setTask(taskEl, '任务失败：' + (job.error || '未知错误'), 'fail');
        setError('预测/回测任务失败：' + (job.error || '未知错误'));
        return;
      }
      setTimeout(tick, 1000);
    } catch (e) {
      setTask(taskEl, '读取任务失败：' + e.message, 'fail');
      setError('读取任务失败：' + e.message);
    }
  };
  setTimeout(tick, 800);
}
function renderForecastResult(job, r) {
  $('forecast-empty').classList.add('hidden');
  $('backtest-content').classList.add('hidden');
  $('forecast-content').classList.remove('hidden');
  const batch = r.batch || job.batch || state.batch || {};
  $('forecast-title').textContent = '未来 7 天预测';
  $('forecast-subtitle').textContent = '起点 ' + (r.origin || '—') + ' · 请求模型 ' + (r.requested_method || '—') +
    ' · 实际模型 ' + (r.actual_method || '—') + '（' + (r.method_label || '') + '）· 版本 ' + (r.model_version || '—') +
    ' · 批次 ' + (batch.label || batch.id || '—');
  renderWarnings('forecast-warnings', r.warnings || []);
  const m = r.metrics || {}, b = r.baseline_metrics || {};
  $('forecast-kpis').innerHTML =
    kpiCard('WAPE', pct(m.WAPE), '基准（4周均值）：' + pct(b.WAPE)) +
    kpiCard('MAE', fmt(m.MAE, 2), '基准 MAE：' + fmt(b.MAE, 2)) +
    kpiCard('偏差 BIAS', fmt(m.BIAS, 2), '偏差率：' + pct(m.BIAS_PCT)) +
    kpiCard('有实际值天数', fmt(r.actual_days) + ' / ' + fmt(r.horizon || 7), r.complete_actual ? '7 天实际值完整' : '末端日期无实际值，不参与评分');
  $('forecast-chart-title').textContent = '预测 / 基准 / 实际';
  $('forecast-chart-badge').textContent = (r.horizon || 7) + ' DAYS';
  const rows = (r.rows || []).map((x) => ({
    date: x.date, prediction: isNum(x.prediction) ? x.prediction : null,
    baseline: isNum(x.baseline) ? x.baseline : null, actual: isNum(x.actual) ? x.actual : null, h: x.h
  }));
  drawLineChart($('forecast-chart'), rows, [
    { key: 'prediction', label: '预测', color: '#167967', digits: 0 },
    { key: 'baseline', label: '4周均值基准', color: '#b8934a', dashed: true, digits: 0 },
    { key: 'actual', label: '实际', color: '#4a6169', digits: 0 }
  ]);
  $('forecast-table-head').innerHTML = '<tr><th>日期</th><th class="num">h</th><th class="num">预测</th><th class="num">基准</th><th class="num">实际</th><th class="num">绝对误差</th></tr>';
  $('forecast-table-body').innerHTML = rows.length ? rows.map((x, i) => {
    const raw = (r.rows || [])[i] || {};
    return '<tr><td>' + esc(x.date) + '</td><td class="num">' + fmt(x.h) + '</td><td class="num">' + fmt(x.prediction, 1) +
      '</td><td class="num">' + fmt(x.baseline, 1) + '</td><td class="num">' + (x.actual === null ? '—' : fmt(x.actual, 0)) +
      '</td><td class="num">' + (isNum(raw.abs_error) ? fmt(raw.abs_error, 1) : '—') + '</td></tr>';
  }).join('') : '<tr><td colspan="6" class="empty-cell">无逐日结果</td></tr>';
  $('forecast-lineage').textContent = '历史：' + (r.history_start || '—') + ' ~ ' + (r.history_end || '—') +
    '（' + fmt(r.history_days) + ' 天）· 训练行数 ' + fmt(r.training_rows) + ' · 模型版本 ' + (r.model_version || '—') +
    ' · 批次 ' + (batch.label || batch.id || '—') + ' · 回退星期（零销量规则）' + ((r.closed_weekdays || []).join(',') || '无') +
    '。预测为固定模型实验，不代表经营收益。';
  $('forecast-export').disabled = false;
}
function renderBacktestResult(job, r) {
  $('forecast-empty').classList.add('hidden');
  $('forecast-content').classList.add('hidden');
  $('backtest-content').classList.remove('hidden');
  const batch = r.batch || job.batch || state.batch || {};
  $('backtest-subtitle').textContent = '起点区间 ' + (r.start || '—') + ' ~ ' + (r.end || '—') + ' · 模型 ' +
    (r.requested_method || '—') + ' · 版本 ' + (r.model_version || '—') + ' · 批次 ' + (batch.label || batch.id || '—');
  renderWarnings('backtest-warnings', r.warnings || []);
  const m = r.metrics || {}, b = r.baseline_metrics || {};
  $('backtest-kpis').innerHTML =
    kpiCard('WAPE', pct(m.WAPE), '基准：' + pct(b.WAPE)) +
    kpiCard('MAE', fmt(m.MAE, 2), '基准 MAE：' + fmt(b.MAE, 2)) +
    kpiCard('偏差率', pct(m.BIAS_PCT), '平均 BIAS：' + fmt(m.BIAS, 2)) +
    kpiCard('评分目标数', fmt(r.scored_count), '起点 ' + fmt(r.origins_attempted) + ' 个 · 完整窗口 ' + fmt(r.complete_origins));
  const by = r.by_horizon || {};
  const grid = [];
  for (let h = 1; h <= 7; h++) {
    const s = by[String(h)] || {};
    grid.push('<div><span>h=' + h + '</span><strong>' + pct(s.WAPE) + '</strong><small>MAE ' + fmt(s.MAE, 1) +
      ' · n ' + fmt(s.n) + '</small></div>');
  }
  $('horizon-grid').innerHTML = grid.join('');
  const worst = r.worst_cases || [];
  $('backtest-worst').innerHTML = worst.length ? worst.map((w) =>
    '<tr><td>' + esc(w.date) + '</td><td>' + esc(w.origin || '—') + '</td><td class="num">' + fmt(w.h) +
    '</td><td class="num">' + fmt(w.actual, 0) + '</td><td class="num">' + fmt(w.prediction, 1) +
    '</td><td class="num">' + fmt(w.abs_error, 1) + '</td></tr>').join('') :
    '<tr><td colspan="6" class="empty-cell">没有可评分的回测目标</td></tr>';
  $('backtest-export').disabled = false;
}
async function runBacktest() {
  const start = $('backtest-start').value, end = $('backtest-end').value;
  const model = $('forecast-model').value;
  if (!start || !end) { setError('请选择回测起止日期'); return; }
  if (start > end) { setError('回测首个起点不能晚于最后起点'); return; }
  const gap = daysBetween(start, end);
  if (gap < 0 || gap > 55) { setError('回测起点区间须为 1 至 56 个日历日'); return; }
  const btn = $('run-backtest');
  busyButton(btn, true);
  clearMessages();
  setTask($('backtest-task'), '正在提交回测任务…', 'busy');
  try {
    const data = await api('/api/backtest', { method: 'POST', body: { start, end, model } });
    const jobId = data.job_id;
    if (!jobId) throw new ApiError('回测提交响应缺少 job_id', 0);
    state.backtestJobId = jobId;
    setTask($('backtest-task'), '任务已排队：' + jobId + ' · 批次 ' + (data.batch_id || '—'), 'busy');
    pollForecastJob(jobId, 'backtest');
  } catch (e) {
    setTask($('backtest-task'), '提交失败：' + e.message, 'fail');
    setError('回测提交失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}

/* ============================== 模拟库存 ============================== */
function newIdempotencyKey(prefix) {
  return (prefix || 'ui') + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
}
async function loadInventoryPage() {
  await loadScenarios(false);
}
function scenarioApi(path) {
  return '/api/inventory/scenarios/' + encodeURIComponent(state.scenarioId) + path;
}
async function loadScenarios(force) {
  const sel = $('scenario-select');
  try {
    const d = await api('/api/inventory/scenarios');
    const list = d.scenarios || [];
    if (!list.length) {
      sel.innerHTML = '<option value="">（暂无场景，请新建）</option>';
      state.scenarioId = null;
      $('scenario-summary').innerHTML = '<p>暂无场景。可在左侧“新建场景”，并从当前批次热销商品建立商品清单。</p>';
      renderItemsEmpty();
      return;
    }
    const old = state.scenarioId || sel.value;
    sel.innerHTML = list.map((s) => '<option value="' + esc(s.id) + '">' + esc(s.name) + '（' + fmt(s.item_count) + ' 商品）</option>').join('');
    const keep = old && list.some((s) => s.id === old) ? old : list[0].id;
    sel.value = keep;
    if (force || state.scenarioId !== keep || !state.items.length) await selectScenario(keep);
    else state.scenarioId = keep;
  } catch (e) {
    sel.innerHTML = '<option value="">场景读取失败</option>';
    renderItemsEmpty('场景读取失败：' + e.message);
  }
}
function renderItemsEmpty(text) {
  $('items-body').innerHTML = '<tr><td colspan="10" class="empty-cell">' + esc(text || '先选择一个场景') + '</td></tr>';
  $('replenish-body').innerHTML = '<tr><td colspan="8" class="empty-cell">' + esc(text || '先选择一个场景') + '</td></tr>';
  $('in-transit-body').innerHTML = '<tr><td colspan="5" class="empty-cell">' + esc(text || '先选择一个场景') + '</td></tr>';
  $('movements-body').innerHTML = '<tr><td colspan="6" class="empty-cell">' + esc(text || '先选择一个场景') + '</td></tr>';
}
async function selectScenario(id) {
  state.scenarioId = id;
  $('scenario-select').value = id;
  renderItemsEmpty('加载中…');
  try {
    const [itemsData, replenish, transit, movements] = await Promise.all([
      api(scenarioApi('/items')),
      api(scenarioApi('/replenishment')),
      api(scenarioApi('/in-transit')),
      api(scenarioApi('/movements'))
    ]);
    state.scenario = itemsData.scenario || null;
    state.items = itemsData.items || [];
    state.itemsMap = {};
    state.items.forEach((it) => { state.itemsMap[it.stock_code] = it; });
    state.replenishment = replenish.items || [];
    state.replenishMap = {};
    state.replenishment.forEach((it) => { state.replenishMap[it.stock_code] = it; });
    renderScenarioSummary();
    renderItems();
    renderReplenishment(replenish);
    renderInTransit(transit.items || []);
    renderMovements(movements.movements || []);
    if (!$('movement-idem').value) $('movement-idem').value = newIdempotencyKey('move');
  } catch (e) {
    renderItemsEmpty('场景数据读取失败：' + e.message);
    setError('模拟库存读取失败：' + e.message);
  }
}
function renderScenarioSummary() {
  const s = state.scenario || {};
  $('scenario-summary').innerHTML = '<p><b>当前场景</b>：' + esc(s.name || state.scenarioId || '—') +
    (s.note ? ' · ' + esc(s.note) : '') + '</p><p>商品 ' + fmt(state.items.length) +
    ' 个 · 接口返回 <span class="badge simulated">simulated: true</span>（模拟，不代表真实库存）</p>';
}
function renderItems() {
  const body = $('items-body');
  if (!state.items.length) { renderItemsEmpty('该场景暂无商品，请新建场景并选择商品'); return; }
  body.innerHTML = state.items.map((it) =>
    '<tr data-sku="' + esc(it.stock_code) + '"><td class="mono">' + esc(it.stock_code) + '</td><td>' + esc(it.name || '—') +
    '</td><td class="num"><input type="number" min="0" step="1" value="' + num(it.initial_qty) + '" data-field="initial_qty"></td>' +
    '<td class="num"><input type="number" min="0" max="365" step="1" value="' + num(it.lead_time_days) + '" data-field="lead_time_days"></td>' +
    '<td class="num"><input type="number" min="0" step="1" value="' + num(it.safety_stock) + '" data-field="safety_stock"></td>' +
    '<td class="num"><input type="number" min="0" step="1" value="' + num(it.daily_demand) + '" data-field="daily_demand"></td>' +
    '<td class="num"><b>' + fmt(it.balance) + '</b></td><td class="num">' + fmt(it.in_transit_effective) + '</td>' +
    '<td class="num">' + fmt(it.replenishment_candidate) + '</td><td class="btn-cell"><button class="button tiny secondary" type="button" data-save="' +
    esc(it.stock_code) + '">保存参数</button></td></tr>'
  ).join('');
  $('items-count').textContent = state.items.length + ' 个商品';
  const sel = $('movement-sku');
  const old = sel.value;
  sel.innerHTML = state.items.map((it) => '<option value="' + esc(it.stock_code) + '">' + esc(it.stock_code + ' · ' + (it.name || '')) + '</option>').join('');
  if (old && state.itemsMap[old]) sel.value = old;
}
async function saveItem(stockCode, btn) {
  const row = document.querySelector('#items-body tr[data-sku="' + CSS.escape(stockCode) + '"]');
  if (!row) return;
  const payload = {};
  row.querySelectorAll('[data-field]').forEach((input) => {
    const v = input.value.trim();
    payload[input.dataset.field] = v === '' ? 0 : Number(v);
  });
  busyButton(btn, true);
  try {
    await api(scenarioApi('/items/' + encodeURIComponent(stockCode)), { method: 'PUT', body: payload });
    toast('已保存商品参数：' + stockCode);
    await Promise.all([loadItems(), loadReplenishment()]);
  } catch (e) {
    setError('保存商品参数失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
async function loadItems() {
  if (!state.scenarioId) return;
  const d = await api(scenarioApi('/items'));
  state.items = d.items || [];
  state.itemsMap = {};
  state.items.forEach((it) => { state.itemsMap[it.stock_code] = it; });
  renderItems();
}
async function createScenario(event) {
  event.preventDefault();
  const name = $('scenario-name').value.trim();
  if (!name) { setError('请输入场景名称'); return; }
  const topn = Math.max(0, Math.min(50, Number($('scenario-topn').value) || 0));
  const payload = { name, note: $('scenario-note').value.trim() };
  if (topn > 0) {
    const products = (state.options && state.options.products) || [];
    if (!products.length) { setError('当前批次没有可用商品列表，无法自动建立商品清单'); return; }
    payload.stock_codes = products.slice(0, topn).map((p) => p.stock_code);
  }
  const btn = event.target.querySelector('button[type="submit"]');
  busyButton(btn, true);
  clearMessages();
  try {
    const d = await api('/api/inventory/scenarios', { method: 'POST', body: payload });
    const s = d.scenario || {};
    state.scenarioId = s.id || null;
    $('scenario-form').reset();
    $('scenario-topn').value = '5';
    toast('已新建模拟场景：' + (s.name || ''));
    await loadScenarios(true);
  } catch (e) {
    setError('新建场景失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
async function submitMovement(event) {
  event.preventDefault();
  if (!state.scenarioId) { setError('请先选择或新建一个模拟场景'); return; }
  const stockCode = $('movement-sku').value;
  if (!stockCode) { setError('请选择商品'); return; }
  const kind = $('movement-kind').value;
  const qty = Number($('movement-qty').value);
  if (!Number.isFinite(qty) || qty <= 0) { setError('数量必须大于 0'); return; }
  const idem = $('movement-idem').value.trim();
  const payload = { stock_code: stockCode, kind, qty, note: $('movement-note').value.trim() };
  if (idem) payload.idempotency_key = idem;
  const btn = event.target.querySelector('button[type="submit"]');
  busyButton(btn, true);
  try {
    const d = await api(scenarioApi('/movements'), { method: 'POST', body: payload });
    const item = d.item || {};
    const dup = d.duplicate === true;
    setTask($('movement-result'), dup
      ? '幂等键命中：未重复记账。商品 ' + stockCode + ' 当前余额 ' + fmt(item.balance) + '（模拟）。'
      : (kind === 'in' ? '入库成功' : '出库成功') + '：' + stockCode + ' ' + fmt(qty) + ' 件，当前余额 ' + fmt(item.balance) + '（模拟）。',
      dup ? 'ok' : 'ok');
    $('movement-idem').value = newIdempotencyKey('move');
    await Promise.all([loadItems(), loadMovements(), loadReplenishment(), loadInTransit()]);
  } catch (e) {
    setTask($('movement-result'), '提交失败：' + e.message, 'fail');
    setError('库存流水提交失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
function renderReplenishment(d) {
  const formula = d.formula || 'max(0, 提前期内场景需求 + 安全库存 - 当前库存 - 有效在途量)';
  $('replenish-formula').innerHTML = '公式（模拟）：<span class="replenish-formula">' + esc(formula) + '</span>';
  const items = d.items || [];
  if (!items.length) {
    $('replenish-body').innerHTML = '<tr><td colspan="8" class="empty-cell">该场景暂无补货建议（没有商品）</td></tr>';
    return;
  }
  $('replenish-body').innerHTML = items.map((it) => {
    const lead = state.itemsMap[it.stock_code] ? Number(state.itemsMap[it.stock_code].lead_time_days) : 7;
    const expected = addDays(todayISO(), Number.isFinite(lead) ? lead : 7);
    const candidate = Number(it.candidate) || 0;
    return '<tr data-sku="' + esc(it.stock_code) + '"><td class="mono">' + esc(it.stock_code) + '</td><td>' + esc(it.name || '—') +
      '</td><td class="num">' + fmt(it.current_qty) + '</td><td class="num">' + fmt(it.lead_time_demand) + '</td>' +
      '<td class="num">' + fmt(it.effective_in_transit) + '</td><td class="num">' + fmt(it.safety_stock) + '</td>' +
      '<td class="num"><b>' + fmt(candidate) + '</b></td><td><div class="replenish-form">' +
      '<input type="number" min="1" step="1" value="' + (candidate > 0 ? candidate : 1) + '" data-rqty="' + esc(it.stock_code) + '">' +
      '<input type="date" value="' + expected + '" data-rdate="' + esc(it.stock_code) + '">' +
      '<button class="button tiny secondary" type="button" data-confirm="' + esc(it.stock_code) + '">确认</button>' +
      '</div></td></tr>';
  }).join('');
}
async function loadReplenishment() {
  if (!state.scenarioId) return;
  const d = await api(scenarioApi('/replenishment'));
  state.replenishment = d.items || [];
  state.replenishMap = {};
  state.replenishment.forEach((it) => { state.replenishMap[it.stock_code] = it; });
  renderReplenishment(d);
}
async function confirmReplenishment(stockCode, btn) {
  if (!state.scenarioId) return;
  const qtyInput = document.querySelector('[data-rqty="' + CSS.escape(stockCode) + '"]');
  const dateInput = document.querySelector('[data-rdate="' + CSS.escape(stockCode) + '"]');
  const qty = qtyInput ? Number(qtyInput.value) : NaN;
  const expected = dateInput ? dateInput.value : '';
  if (!Number.isFinite(qty) || qty <= 0) { setError('补货数量必须大于 0'); return; }
  if (!expected) { setError('请选择预计到货日期'); return; }
  busyButton(btn, true);
  try {
    await api(scenarioApi('/replenishment/confirm'), {
      method: 'POST',
      body: { stock_code: stockCode, qty, expected_date: expected, idempotency_key: newIdempotencyKey('replenish') }
    });
    toast('已登记在途补货：' + stockCode + ' × ' + qty + '（模拟，未直接增加库存）');
    await Promise.all([loadReplenishment(), loadInTransit(), loadItems()]);
  } catch (e) {
    setError('确认补货失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
async function loadInTransit() {
  if (!state.scenarioId) return;
  const d = await api(scenarioApi('/in-transit'));
  renderInTransit(d.items || []);
}
function renderInTransit(items) {
  const body = $('in-transit-body');
  if (!items.length) { body.innerHTML = '<tr><td colspan="5" class="empty-cell">暂无在途记录</td></tr>'; return; }
  body.innerHTML = items.map((it) =>
    '<tr><td class="mono">' + esc(it.stock_code) + '</td><td class="num">' + fmt(it.qty) + '</td><td>' + esc(it.expected_date || '—') +
    '</td><td>' + esc(it.status || '—') + '</td><td>' + (it.status === 'in_transit'
      ? '<button class="button tiny secondary" type="button" data-receive="' + esc(it.id) + '">到货入库</button>' : '已完成') +
    '</td></tr>').join('');
}
async function receiveInTransit(id, btn) {
  if (!state.scenarioId) return;
  busyButton(btn, true);
  try {
    const d = await api(scenarioApi('/in-transit/' + encodeURIComponent(id) + '/receive'), { method: 'POST' });
    const item = d.item || {};
    toast('已到货入库，余额更新为 ' + fmt(item.balance) + '（模拟）');
    await Promise.all([loadItems(), loadInTransit(), loadMovements(), loadReplenishment()]);
  } catch (e) {
    setError('到货入库失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
async function loadMovements() {
  if (!state.scenarioId) return;
  const d = await api(scenarioApi('/movements'));
  renderMovements(d.movements || []);
}
function renderMovements(rows) {
  const body = $('movements-body');
  if (!rows.length) { body.innerHTML = '<tr><td colspan="6" class="empty-cell">暂无库存流水</td></tr>'; return; }
  body.innerHTML = rows.map((r) =>
    '<tr><td class="tiny">' + esc(isoTime(r.created_at)) + '</td><td class="mono">' + esc(r.stock_code) + '</td><td>' +
    (r.kind === 'in' ? '<span class="badge ok">入库</span>' : '<span class="badge neutral">出库</span>') +
    '</td><td class="num">' + fmt(r.qty) + '</td><td class="num">' + fmt(r.balance_after) + '</td><td class="tiny mono">' +
    esc(r.idempotency_key || '—') + (r.note ? '<br>' + esc(r.note) : '') + '</td></tr>').join('');
}

/* ============================== AI 助手 ============================== */
async function loadAiPage() {
  await Promise.all([loadAiStatus(false), loadAiTools()]);
}
async function loadAiStatus(showToast) {
  const banner = $('ai-banner');
  try {
    const st = await api('/api/ai/status');
    state.aiStatus = st;
    if (st.llm_configured) {
      banner.className = 'ai-banner configured';
      banner.innerHTML = '<b>已配置真实大模型</b><span class="llm-tag">provider=' + esc(st.provider || '—') + '</span> ' +
        '模型 <b>' + esc(st.model || '—') + '</b> · 超时 ' + esc(st.timeout_seconds || '—') + 's · 密钥' +
        (st.key_present ? '已配置（页面不回显）' : '未配置') + ' · 可用工具 ' + fmt(st.tools_count) + ' 个。' +
        '回答中的数字均来自只读工具结果；失败时保留工具结果与错误原因。';
    } else {
      banner.className = 'ai-banner not-configured';
      banner.innerHTML = '<b>未配置真实大模型</b><span class="llm-tag">未调用大模型</span> ' +
        '当前回答由本地规则工具层（provider=<b>' + esc(st.provider || 'local-rule-planner') + '</b>' +
        (st.fallback ? '，fallback=' + esc(st.fallback) : '') + '）生成：仅把问题映射到声明的只读工具并引用工具数字，' +
        '不伪造自然语言智能结论。密钥状态：' + (st.key_present ? '已配置（但缺少完整配置）' : '未配置') +
        ' · 可用工具 ' + fmt(st.tools_count) + ' 个。';
    }
    if (showToast) toast('AI 状态已刷新');
  } catch (e) {
    banner.className = 'ai-banner not-configured';
    banner.innerHTML = '<b>AI 状态接口未就绪</b>：' + esc(e.message) + '。本轮未验证 AI 功能；未配置真实大模型时不调用大模型。';
  }
}
async function loadAiTools() {
  const el = $('ai-tools');
  try {
    const d = await api('/api/ai/tools');
    const tools = d.tools || [];
    state.aiTools = tools;
    el.innerHTML = tools.length ? tools.map((t) => '<li><b class="mono">' + esc(t.name) + '</b> — ' + esc(t.description || '') +
      (t.read_only ? ' <span class="badge ok">只读</span>' : '') + '</li>').join('') : '<li>未声明工具</li>';
    if (d.policy) el.innerHTML += '<li class="muted">策略：' + esc(d.policy) + '</li>';
  } catch (e) {
    el.innerHTML = '<li>工具清单未就绪：' + esc(e.message) + '</li>';
  }
}
async function askAi(event) {
  event.preventDefault();
  const question = $('ai-question').value.trim();
  if (!question) { setError('请输入问题'); return; }
  const scope = {};
  if ($('ai-start').value) scope.start = $('ai-start').value;
  if ($('ai-end').value) scope.end = $('ai-end').value;
  if ($('ai-product').value.trim()) scope.product = $('ai-product').value.trim();
  if ($('ai-country').value) scope.country = $('ai-country').value;
  const btn = $('ai-submit');
  busyButton(btn, true);
  clearMessages();
  $('ai-answer').innerHTML = '<div class="empty-cell">正在调用只读工具并生成回答…</div>';
  try {
    const body = { question };
    if (Object.keys(scope).length) body.scope = scope;
    const d = await api('/api/ai/ask', { method: 'POST', body });
    renderAiAnswer(d, question);
  } catch (e) {
    $('ai-answer').innerHTML = '<div class="alert error">提问失败：' + esc(e.message) + '</div>';
    setError('AI 提问失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
function renderAiAnswer(d, question) {
  const status = d.status || 'unknown';
  const statusLabel = { ok: '正常回答', need_clarification: '需要澄清', failed: '调用失败', not_configured: '未配置' }[status] || status;
  const badgeCls = status === 'ok' ? 'ok' : (status === 'failed' ? 'fail' : 'neutral');
  const range = d.range || {};
  const batch = d.batch || state.batch || {};
  const rangeText = [range.start, range.end].filter(Boolean).join(' ~ ') || '未指定（按工具默认）';
  let html = '<div class="answer-meta">' +
    '<span class="badge ' + badgeCls + '">' + esc(statusLabel) + '</span>' +
    '<span>provider：<b>' + esc(d.provider || '—') + '</b></span>' +
    '<span>大模型调用：<b>' + (d.llm_used ? '是' : '否（本地规则工具层）') + '</b></span>' +
    '<span>范围：' + esc(rangeText) + '</span>' +
    '<span>批次：' + esc(batch.label || batch.id || '—') + '</span>' +
    (d.elapsed_seconds !== undefined ? '<span>耗时：' + fmt(d.elapsed_seconds, 2) + 's</span>' : '') + '</div>';
  if (d.clarification) html += '<div class="ai-clarification"><b>需要补充信息：</b>' + esc(d.clarification) + '</div>';
  if (d.error) html += '<div class="alert error">错误：' + esc(d.error) + '</div>';
  if (d.answer) html += '<div class="answer-text">' + esc(d.answer) + '</div>';
  else if (!d.clarification && !d.error) html += '<div class="empty-cell">没有返回回答正文</div>';
  const calls = d.tool_calls || [];
  if (calls.length) {
    html += '<div><div class="panel-head" style="margin:0 0 8px"><h3>工具调用轨迹（' + calls.length + '）</h3></div>' +
      '<div class="tool-trace">' +
      calls.map((c) => '<div class="tool-call">' +
        '<div class="tool-call-head"><span class="mono"><b>' + esc(c.name) + '</b></span>' +
        (c.ok ? '<span class="badge ok">成功</span>' : '<span class="badge fail">失败</span>') + '</div>' +
        '<div class="params mono">参数：' + esc(JSON.stringify(c.params || {})) + '</div>' +
        '<div class="summary">' + esc(c.summary || '—') + '</div>' +
        '</div>').join('') + '</div></div>';
  }
  const warnings = d.warnings || [];
  if (warnings.length) {
    html += '<div class="warnings">' + warnings.map((w) => '<div class="warning-item">' + esc(w) + '</div>').join('') + '</div>';
  }
  html += '<div class="tiny muted">问题：' + esc(question) + '</div>';
  $('ai-answer').innerHTML = html;
}
async function createBrief() {
  const btn = $('ai-brief');
  busyButton(btn, true);
  clearMessages();
  $('ai-brief-result').innerHTML = '<p class="muted small">正在生成运营简报…</p>';
  try {
    const scope = {};
    if ($('ai-start').value) scope.start = $('ai-start').value;
    if ($('ai-end').value) scope.end = $('ai-end').value;
    const body = Object.keys(scope).length ? { scope } : {};
    const d = await api('/api/ai/brief', { method: 'POST', body });
    const brief = d.brief || {};
    const md = d.markdown || '';
    $('ai-brief-result').innerHTML =
      '<p><b>' + esc(brief.title || '运营简报') + '</b> · ' + esc(isoTime(brief.created_at)) +
      ' · provider=' + esc(d.provider || 'template') + '</p>' +
      '<div class="brief-preview">' + esc(md.slice(0, 2200)) + (md.length > 2200 ? '\n…（完整内容请下载）' : '') + '</div>' +
      '<button class="button secondary" type="button" id="ai-brief-download">↓ 下载 Markdown 简报</button>';
    $('ai-brief-download').addEventListener('click', () => {
      const url = brief.url || ('/api/ai/briefs/' + encodeURIComponent(brief.id) + '.md');
      downloadFile(url, (brief.title || 'brief') + '.md').catch((e) => setError('简报下载失败：' + e.message));
    });
    toast('运营简报已生成');
  } catch (e) {
    $('ai-brief-result').innerHTML = '<div class="alert error">生成简报失败：' + esc(e.message) + '</div>';
    setError('生成简报失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}

/* ============================== 系统管理 ============================== */
async function loadAdminPage() {
  if (!state.user || state.user.role !== 'admin') { go('dashboard'); return; }
  await Promise.all([loadUsers(), loadSettings()]);
}
async function loadUsers() {
  const body = $('users-body');
  body.innerHTML = '<tr><td colspan="7" class="empty-cell">加载中…</td></tr>';
  try {
    const d = await api('/api/users');
    const users = d.users || [];
    $('users-count').textContent = users.length + ' 个账号';
    body.innerHTML = users.map((u) =>
      '<tr data-uid="' + esc(u.id) + '"><td class="mono">' + esc(u.id) + '</td><td>' + esc(u.username) +
      '</td><td>' + esc(u.display_name || '—') + '</td><td>' + userRoleSelect(u) + '</td><td>' +
      (u.active ? '<span class="badge ok">启用</span>' : '<span class="badge fail">停用</span>') +
      (u.must_change_password ? ' <span class="tiny muted">需改密</span>' : '') +
      '</td><td><div class="user-actions">' +
      '<button class="button tiny secondary" type="button" data-toggle="' + esc(u.id) + '" data-active="' + (u.active ? '1' : '0') + '">' +
      (u.active ? '停用' : '启用') + '</button>' +
      '<button class="button tiny ghost" type="button" data-password="' + esc(u.id) + '">重置密码</button>' +
      '</div></td></tr>').join('') || '<tr><td colspan="6" class="empty-cell">暂无账号</td></tr>';
  } catch (e) {
    body.innerHTML = '<tr><td colspan="6" class="empty-cell">账号读取失败：' + esc(e.message) + '</td></tr>';
    setError('账号读取失败：' + e.message);
  }
}
function userRoleSelect(u) {
  return '<select class="role-select" data-role="' + esc(u.id) + '">' +
    '<option value="admin"' + (u.role === 'admin' ? ' selected' : '') + '>admin</option>' +
    '<option value="operator"' + (u.role === 'operator' ? ' selected' : '') + '>operator</option></select>';
}
async function onUserAction(event) {
  const toggle = event.target.closest('[data-toggle]');
  const pw = event.target.closest('[data-password]');
  if (toggle) {
    const id = toggle.dataset.toggle;
    const active = toggle.dataset.active === '1';
    if (!confirm((active ? '确认停用' : '确认启用') + '该账号？')) return;
    busyButton(toggle, true);
    try {
      await api('/api/users/' + encodeURIComponent(id), { method: 'PATCH', body: { active: !active } });
      toast('账号状态已更新');
      await loadUsers();
    } catch (e) {
      setError('更新账号失败：' + e.message);
      busyButton(toggle, false);
    }
  } else if (pw) {
    const id = pw.dataset.password;
    const password = prompt('输入新密码（至少 8 位）：');
    if (password === null) return;
    if (password.length < 8) { setError('新密码至少 8 位'); return; }
    busyButton(pw, true);
    try {
      await api('/api/users/' + encodeURIComponent(id), { method: 'PATCH', body: { password } });
      toast('密码已重置');
    } catch (e) {
      setError('重置密码失败：' + e.message);
    } finally {
      busyButton(pw, false);
    }
  }
}
async function onUserRoleChange(event) {
  const sel = event.target.closest('[data-role]');
  if (!sel) return;
  const id = sel.dataset.role;
  const role = sel.value;
  try {
    await api('/api/users/' + encodeURIComponent(id), { method: 'PATCH', body: { role } });
    toast('角色已更新为 ' + role);
    await loadUsers();
  } catch (e) {
    setError('修改角色失败：' + e.message);
    await loadUsers();
  }
}
async function createUser(event) {
  event.preventDefault();
  const username = $('new-username').value.trim();
  const password = $('new-password').value;
  if (!username) { setError('请输入用户名'); return; }
  if (password.length < 8) { setError('初始密码至少 8 位'); return; }
  const btn = event.target.querySelector('button[type="submit"]');
  busyButton(btn, true);
  clearMessages();
  try {
    const d = await api('/api/users', {
      method: 'POST',
      body: {
        username, password, role: $('new-role').value,
        display_name: $('new-display').value.trim()
      }
    });
    setTask($('user-form-result'), '已创建账号：' + ((d.user || {}).username || username), 'ok');
    $('user-form').reset();
    $('new-role').value = 'operator';
    await loadUsers();
  } catch (e) {
    setTask($('user-form-result'), '创建失败：' + e.message, 'fail');
    setError('创建账号失败：' + e.message);
  } finally {
    busyButton(btn, false);
  }
}
async function loadSettings() {
  const dl = $('settings-list');
  try {
    const d = await api('/api/settings');
    const rows = [];
    const ai = d.ai || d.ai_config || {};
    if (d.version) rows.push(['版本', d.version]);
    if (d.service) rows.push(['服务', d.service]);
    if (d.data_ready !== undefined) rows.push(['数据就绪', d.data_ready ? '是' : '否']);
    if (d.batch) rows.push(['当前批次', (d.batch.label || d.batch.id || '—')]);
    Object.entries(ai).forEach(([k, v]) => {
      const label = { configured: 'AI 已配置', provider: 'AI provider', model: 'AI 模型', base_url: 'AI base_url', timeout_seconds: 'AI 超时(s)', key_present: 'AI 密钥存在' }[k] || ('ai.' + k);
      rows.push([label, typeof v === 'object' ? JSON.stringify(v) : (typeof v === 'boolean' ? (v ? '是' : '否') : String(v))]);
    });
    Object.entries(d).forEach(([k, v]) => {
      if (['ai', 'ai_config', 'version', 'service', 'data_ready', 'batch'].includes(k)) return;
      rows.push([k, typeof v === 'object' ? JSON.stringify(v) : String(v)]);
    });
    if (!rows.length) rows.push(['设置', '接口返回空对象']);
    dl.innerHTML = rows.map(([k, v]) => '<dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd>').join('');
  } catch (e) {
    dl.innerHTML = '<dt>只读设置不可用</dt><dd>' + esc(e.message) + '（本页如实显示：设置接口未就绪，未做伪造展示）</dd>';
    if (state.health) {
      dl.innerHTML += '<dt>服务版本</dt><dd>' + esc(state.health.version || '—') + '</dd>' +
        '<dt>数据就绪</dt><dd>' + (state.health.data_ready ? '是' : '否') + '</dd>' +
        '<dt>AI 已配置</dt><dd>' + (state.health.ai_configured ? '是' : '否（未调用大模型）') + '</dd>';
    }
  }
}

/* ============================== 启动 ============================== */
async function boot() {
  try {
    $('login-form').addEventListener('submit', doLogin);
    $('login-username').focus();
  } catch (e) { /* ignore */ }
  await checkHealth();
  try {
    const me = await api('/api/auth/me', { quiet401: true });
    if (me && me.user) {
      state.user = me.user;
      await enterApp();
      return;
    }
  } catch (e) {
    if (e.status !== 401) {
      $('login-error').textContent = '会话检查失败：' + e.message;
      $('login-error').classList.remove('hidden');
    }
  }
  showLogin('');
}

document.addEventListener('DOMContentLoaded', boot);
