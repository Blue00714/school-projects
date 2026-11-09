# Vue 3 + Element Plus 前端迁移验收笔记（frontend / task-8）

- 日期：2026-10-29（Asia/Shanghai）
- 模板：mall-admin-web v3.0.0（项目内 `电商运营系统/frontend/`，Vue 3.5 / Element Plus 2.12 / Vite 7 / Pinia / Vue Router / ECharts / TS）
- 交付：`frontend/src` 业务源码 + `frontend/dist` 构建产物；FastAPI 检测到 `dist/index.html` 时优先服务 `/` 与 `/assets/*`（当前已验证）
- 后端：SQLite 模式联调（接口契约不变；MySQL 后端由 mysql-backend 并行迁移），未改 `api/`、`services/`、`web/`、`runtime/`、原始数据

## 1. 构建结果与环境配方

```powershell
cd 电商运营系统\frontend
# 依赖（沙箱需 --ignore-scripts，cache 放工作区）
$env:npm_config_cache="...\.tmp\npm-cache"; $env:TMP="$env:npm_config_cache\tmp"; $env:TEMP=$env:TMP
& "C:\Program Files\nodejs\npm.cmd" install --ignore-scripts --registry=https://registry.npmmirror.com --no-audit --no-fund
# 构建（本机沙箱用 build.mjs 内联配置；普通机器也可直接 node build.mjs）
& "C:\Program Files\nodejs\node.exe" build.mjs
```

- 结果：**vite build 成功**，`dist/index.html` + `dist/assets/*`（52 个文件，约 5.5 MB，未压缩/未做 sourcemap，课程原型可接受）。
- **沙箱限制与绕行（重要，仅影响本机构建方式，不影响业务代码）**：
  1. 本机禁止 Node 子进程管道（`child_process.spawn` 默认 pipe 报 EPERM），原生 esbuild 服务无法启动；
  2. 因此 `vite.config.mjs` 设 `esbuild: false`，由 `vite/ts-plugin.mjs` 用 TypeScript 编译器在进程内转译 `.ts` / `<script lang="ts">`，并同时完成 `process.env.NODE_ENV`、`__VUE_OPTIONS_API__` 等编译期常量替换；
  3. 配置通过 `build.mjs` 内联传入（`configFile:false`），避开 Vite 配置加载器的 esbuild 打包；
  4. 本机 `node_modules` 另做了两处*环境级*补丁（不随源码交付）：Vite `optimizeSafeRealPathSync` 的 `net use` 调用改为 try/catch；`node_modules/esbuild` 替换为进程内 shim（原文件备份 `main.real.js`）。普通开发机 `npm run build` / `vite build` 无需这些补丁。
- 构建产物**无 CDN / 无外部资源**；图标为本地 SVG sprite，字体与 Element Plus 样式全部内置。

## 2. 改造内容

- 路由/菜单：只保留 7 个入口——看板、商品与订单、数据导入、预测与回测、模拟库存、AI 助手、系统管理（仅 admin）；删除 mall 商城演示模块（pms/oms/sms/ums/home/test）、演示 API/类型、Tinymce/Upload 组件与 public/tinymce 静态包；无 mock 数据。
- `src/apis/ece.ts`：axios 同源相对路径（baseURL 空、withCredentials），直接消费后端裸 JSON；统一读 `{error, code}` 并用 ElMessage 展示；401 清空用户态并跳 `#/login`；CSV/Markdown 下载解析 `Content-Disposition`，错误时解析 blob 里的 JSON。
- `src/stores/user.ts`：Cookie 会话（不持久化 token），保存 user/batch/manifest/options/health；`src/stores/permission.ts`：按角色过滤的 7 项菜单；`src/router/guard.ts`：首屏 `/api/auth/me` 恢复会话、未登录跳登录、admin 路由守卫。
- 布局：保留模板侧边栏/面包屑/主题，Navbar 重写为“批次 label+覆盖范围、AI 是否配置、当前用户+角色、退出”；新增全局口径条（销售额≠利润、无记录日补 0、模拟库存、预测局限）。
- 页面：看板（筛选 + KPI + ECharts 趋势 + 国家汇总 + 商品排名 + 质量提示 + 口径与批次）、商品与订单（商品搜索、订单列表→明细对话框、交易明细、3 个 CSV 导出、分页）、数据导入（上传、轮询、质量摘要/issues、批次列表与切换、模板下载）、预测与回测（模型/起点、任务轮询、7 日表+WAPE/MAE/偏差、回测分步长与高误差、CSV 导出）、模拟库存（场景、参数编辑保存、入出库+幂等、补货公式/确认、在途/到货、流水）、AI 助手（状态横幅、工具清单、提问+范围/批次/工具轨迹/警告、简报生成与下载）、系统管理（用户列表/新建/启停/改角色/重置密码、设置只读且不含密钥）。

## 3. 页面 ↔ 接口对应

| 页面 | 接口 |
|---|---|
| 登录/守护 | `GET /api/health`、`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout` |
| 顶部/批次 | `GET /api/auth/me`、`GET /api/options`（batch + manifest）、`GET /api/health` |
| 看板 | `GET /api/dashboard?start&end&product&country`、`GET /api/export/trend` |
| 商品与订单 | `GET /api/products?q=`、`GET /api/orders`、`GET /api/orders/{order_key}`、`GET /api/transactions`、`GET /api/export/{trend,orders,transactions}` |
| 数据导入 | `POST /api/imports`（multipart）、`GET /api/imports/jobs/{id}`、`GET /api/imports/batches`、`POST /api/imports/batches/{id}/activate`、`GET /api/imports/template.csv` |
| 预测与回测 | `POST /api/forecast`、`POST /api/backtest`、`GET /api/jobs/{id}`、`GET /api/jobs/{id}/export`、`GET /api/options.methods` |
| 模拟库存 | `GET/POST /api/inventory/scenarios`、`GET .../items`、`PUT .../items/{sku}`、`POST/GET .../movements`、`GET .../replenishment`、`POST .../replenishment/confirm`、`GET .../in-transit`、`POST .../in-transit/{iid}/receive` |
| AI 助手 | `GET /api/ai/status`、`GET /api/ai/tools`、`POST /api/ai/ask`、`POST /api/ai/brief`、`GET /api/ai/briefs/{id}.md` |
| 系统管理 | `GET/POST /api/users`、`PATCH /api/users/{id}`、`GET /api/settings` |

## 4. 浏览器实测（实际点击，截图在 `runs/20261029_mysql/screenshots/`）

运行方式：FastAPI 8777，隔离运行目录 `.tmp/rt_vue`（`ECE_ORIGINAL_DB` 指向 `runtime/retail.sqlite3`），`ECE_ADMIN_PASSWORD=VueAdmin2026x`、`ECE_OPERATOR_PASSWORD=VueOperator2026x`；浏览器实际打开 `http://127.0.0.1:8777` 点击。

| 截图 | 实测内容 |
|---|---|
| 01-login.png | 登录页（Vue3+Element Plus 版式、服务状态、口径提示）；admin 登录成功 |
| 02-dashboard.png | 看板：默认 2011-11-10~12-09；销量 851,403 / 销售额 £1,694,068.71 / 订单 2,913 / 平均订单额 £581.55；ECharts 趋势、国家汇总、商品 TOP20、质量提示、口径与批次；顶部批次与 AI 状态正确 |
| 03-orders.png、04-order-detail.png | 订单列表 2,913 单；点击订单 581475 打开明细对话框：19 行、352 件、£280.24，行金额可核对；商品搜索与交易明细页签渲染正常 |
| 05-imports.png、06-imports-batches.png、07-imports-switched.png | 导入页；通过 API 上传小样例（label「Vue 验收样例」）生成批次后，UI「刷新批次」显示新批次，点击「切换」后顶部批次/覆盖范围变为 `Vue 验收样例 · 2011-01-01~2011-01-02`，再切回 original 恢复；duplicate/质量摘要的 UI 分支因文件选择器限制未点击（见第 5 节） |
| 08-forecast.png | 预测（blend_rule，起点 2011-11-02）：任务 1.6s 成功；WAPE 22.8%、MAE 6,175.76、偏差 -6,175.76、有实际值 7/7；ECharts 预测/基准/实际 + 逐日表 + 警告/版本/批次 |
| 09-inventory.png | 模拟库存：新建「Vue 验收场景」（前 5 热销商品，接口返回 simulated:true），保存商品参数（初始库存 100），入库 10 件后余额 100→110，流水与补货建议同步刷新；页面常驻“模拟数据、不代表真实库存/公式”声明 |
| 10-ai.png | AI 助手：横幅明确「未配置真实大模型 / 未调用大模型 / provider=local-rule-planner / 可用工具 7 个」；提问成功，回答含范围、批次、工具调用轨迹（product_ranking 成功、参数与摘要）与警告 |
| 11-admin.png | 系统管理：用户列表、角色下拉、启停/重置密码按钮；设置只读（当前批次、AI 已配置=否、密钥存在=否、会话 TTL），无密钥回显 |
| 12-logout.png | 退出登录回到登录页（“已退出登录”提示，会话 Cookie 已撤销） |
| 13-operator.png | operator 登录后侧栏仅 6 项、无「系统管理」；直接访问 `#/admin` 被守卫重定向回看板（已实测） |

补充说明：
- AI 简报生成已点击成功（201，页面显示标题/时间/预览与下载按钮，提示“运营简报已生成”）；简报预览截图未单独保留（当时页面滚动操作导致重挂载丢失组件状态，非功能失败）。
- 订单明细接口按契约与列表共享 `start/end/product/country` 口径，前端已显式携带筛选参数。
- 切换批次后顶部批次/覆盖范围与全局口径条即时更新；看板/订单下次进入按新批次加载。

## 5. 未验证 / 已知限制（如实）

1. **文件选择器上传的 UI 自动化**：浏览器自动化无法向 Element Plus 隐藏 `<input type=file>` 注入文件（按钮不接收 set_value，点击会打开系统文件对话框）。因此“上传→轮询→质量摘要/issues”的 UI 分支未在浏览器内完成；导入接口用脚本上传真实样例验证成功（batch `b20261029T0802081fc1b9`），批次列表与“切换为当前批次”按钮已在 UI 实测。**未验证**：浏览器内文件选择后的轮询进度与问题清单渲染。
2. **CSV/Markdown 下载**：下载函数已实现并走同源 Cookie；浏览器点击后会下载到 agent 浏览器目录，未逐字节核对文件内容（接口层此前已由旧前端验证 BOM/文件名；本轮未重复检查）。**未验证**：浏览器下载落盘内容。
3. **Element Plus 原生确认框**：用户“停用/重置密码”使用 ElMessageBox；浏览器自动化未逐一点击确认分支（编辑角色下拉与新建账号对话框已渲染；这些写操作此前在旧前端与 API 层验证过），**未验证**：Vue 页面上的实际 PATCH 提交。
4. **MySQL 模式**：本轮按指示用 SQLite 模式联调；MySQL 后端由 mysql-backend 并行迁移，接口契约一致。**未验证**：MySQL 实例下的同一套页面（需后端就绪后回归）。
5. **视口**：agent 浏览器窗口约 1241px 宽，截图按此宽度；1280 桌面布局为 4 列 KPI，窄屏断点（≤1200/1100/850）未逐档截图。
6. **构建可移植性**：`frontend/dist` 已随工作区交付；换机若无 Node 环境可直接用 dist。若换机重新构建，普通环境执行 `npm install` + `node build.mjs`（或 `npm run build`）即可；本机 node_modules 的两处沙箱补丁不会随源码同步，换机不需要。
7. **模板残留**：保留模板的 `src/icons`（本地 SVG）、`src/styles`、`src/stores/app.ts`、`src/utils`（仅用到常量时）；已删除商城 views/apis/types 与 mock、Tinymce/Upload 组件及 public/tinymce 静态包。旧原生前端 `web/` 未删除，作为 FastAPI 的 dist 回退方案保留。

## 6. 复现步骤

1. 启动后端（SQLite 模式即可）：
   ```powershell
   cd 电商运营系统
   $env:PYTHONPATH="$PWD\.pydeps"
   $env:ECE_RUNTIME_DIR="...\.tmp\rt_vue"
   $env:ECE_ORIGINAL_DB="...\电商运营系统\runtime\retail.sqlite3"
   $env:ECE_APP_DB="...\.tmp\rt_vue\app.sqlite3"
   $env:ECE_ADMIN_PASSWORD="VueAdmin2026x"
   $env:ECE_OPERATOR_PASSWORD="VueOperator2026x"
   python run.py --port 8777
   ```
2. 浏览器打开 `http://127.0.0.1:8777`，admin/VueAdmin2026x 登录。
3. 依次点击：看板 → 商品与订单（点订单行看明细）→ 数据导入（刷新批次/切换）→ 预测与回测（生成预测）→ 模拟库存（新建/保存/入出库）→ AI 助手（提问）→ 系统管理 → 退出；再用 operator 登录确认无系统管理菜单。
