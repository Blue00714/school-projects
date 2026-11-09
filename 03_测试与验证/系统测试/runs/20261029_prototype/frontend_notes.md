# 前端单页应用验收笔记（frontend）

- 运行日期：2026-10-29（Asia/Shanghai）
- 交付文件：`电商运营系统/web/index.html`、`web/app.js`、`web/style.css`（原生 HTML/CSS/JS，无框架、无 CDN、无构建）
- 服务入口：FastAPI `python run.py` 提供 `/` 与 `/static/*`；页面资源引用 `/static/style.css`、`/static/app.js`，所有 API 用同源相对路径 `/api/...`（不写死端口）
- 本笔记只记录本轮实际运行/点击过的内容；未验证项在文末如实列出

## 1. 验收环境与启动方式

隔离运行目录（未触碰 `电商运营系统/runtime` 原始库，未改原始 Excel/模型）：

```powershell
cd 电商运营系统
$env:PYTHONPATH           = "...\电商运营系统\.pydeps"
$env:ECE_RUNTIME_DIR      = "...\.tmp\rt_fe"
$env:ECE_ORIGINAL_DB      = "...\电商运营系统\runtime\retail.sqlite3"
$env:ECE_APP_DB           = "...\.tmp\rt_fe\app.sqlite3"
python run.py --port 8785
```

- 登录账号来自隔离目录 `%ECE_RUNTIME_DIR%\initial_admin.txt`（UTF-8）：admin（管理员）、operator（运营）。
- 浏览器：windows_computer_use 浏览器通道，实际点击；视口 1241×808（agent 浏览器窗口），CSS 断点按 1280 桌面优先设计。
- 截图目录：`03_测试与验证/系统测试/runs/20261029_prototype/screenshots/`（01–18，见第 3 节）。
- 后端配合：auth/system/analytics/imports/forecast/inventory/ai 路由在本轮联调中陆续就绪；`GET /api/orders/{order_key}` 曾返回 500（bindings 2 vs 3），已由 data-api 修复后复测通过。

## 2. 页面 ↔ 接口对应关系

| 页面/区域 | 调用接口 | 说明 |
|---|---|---|
| 登录页 | `GET /api/health`（免登录）、`POST /api/auth/login`、`GET /api/auth/me` | 健康状态显示版本/数据就绪/AI 是否配置；登录成功进入主界面；退出 `POST /api/auth/logout` |
| 顶部栏 | `/api/auth/me`、`GET /api/options`（batch+manifest）、`/api/health` | 当前用户+角色、当前批次 label+覆盖范围、课程原型标识、退出登录 |
| 口径条（全局） | 无（来自 manifest/批次） | 销售额≠利润、无记录日补 0、模拟库存、预测局限 |
| 看板 | `GET /api/dashboard?start&end&product&country`、`GET /api/options`（国家/商品） | 总量卡（销量/销售额 GBP/订单数/平均订单额）、SVG 折线趋势（销量/销售额/订单数切换）、商品 TOP20、国家 TOP10、质量提示、警告、口径与批次、无记录日 obs=0 |
| 看板趋势导出 | `GET /api/export/trend` | 同筛选条件 CSV |
| 商品与订单 | `GET /api/products?q=`、`GET /api/orders?start&end&product&country&q&page&page_size`、`GET /api/orders/{order_key}`、`GET /api/transactions?...`、`GET /api/export/{trend,orders,transactions}` | 商品搜索→“用作筛选”；订单列表分页→点击订单查看明细（明细请求显式带 start/end/product/country，与列表口径一致）；交易明细分页；三个 CSV 导出按钮 |
| 数据导入 | `POST /api/imports`（multipart）、`GET /api/imports/jobs/{id}`（轮询）、`GET /api/imports/batches`、`POST /api/imports/batches/{id}/activate`、`GET /api/imports/template.csv` | 上传 xlsx/xls/csv → 轮询 → 质量摘要/issues → 批次列表 → 切换当前批次（切换后刷新顶部批次、看板/订单数据）；duplicate 显示既有批次 |
| 预测与回测 | `POST /api/forecast`、`POST /api/backtest`、`GET /api/jobs/{id}`（轮询）、`GET /api/jobs/{id}/export`、`GET /api/options.methods` | 模型选择、起点、提交 202 后轮询；7 天逐日表+WAPE/MAE/偏差+基准对比+警告；回测区间提交、分步长 WAPE、高误差案例；CSV 导出 |
| 模拟库存 | `GET/POST /api/inventory/scenarios`、`GET /api/inventory/scenarios/{sid}/items`、`PUT .../items/{sku}`、`POST .../movements`、`GET .../movements`、`GET .../replenishment`、`POST .../replenishment/confirm`、`GET .../in-transit`、`POST .../in-transit/{iid}/receive` | 场景选择/新建（可从当前批次热销商品建立清单）、参数编辑保存、入库/出库（幂等键）、流水、补货建议（显示公式+模拟标识）、确认补货、到货入库、在途列表；所有响应 `simulated:true` 在页面标注 |
| AI 助手 | `GET /api/ai/status`、`GET /api/ai/tools`、`POST /api/ai/ask`、`POST /api/ai/brief`、`GET /api/ai/briefs/{id}.md` | 状态横幅（未配置即写明“未调用大模型/本地规则工具层”）、工具清单、提问（范围/批次/工具轨迹/警告）、简报生成+预览+下载 |
| 系统管理（仅 admin） | `GET/POST /api/users`、`PATCH /api/users/{id}`、`GET /api/settings` | 用户列表、新建账号、启停/改角色/重置密码；设置只读展示（AI 配置不含密钥） |
| 通用 | 所有 `/api/*` | 统一解析 `{error, code}` 并在页面显示；401 清会话回登录页；loading/空态/失败态 |

## 3. 浏览器实测记录（实际点击，附截图）

| 编号 | 页面 | 实测结果 |
|---|---|---|
| 01-login.png | 登录页 | 显示服务状态（版本/数据就绪/AI 未配置/当前批次）；账号密码错误时显示后端 error；admin 登录成功进入看板 |
| 02-dashboard.png | 看板 | 默认最近 30 天（2011-11-10~12-09）；销量 851,403、销售额 £1,694,068.71、订单 2,913、平均订单额 £581.55；环比、SVG 趋势、国家汇总、商品 TOP20、质量提示、口径与批次全部渲染 |
| 03-orders.png / 04-order-detail.png | 商品与订单 | 订单列表共 2,913 单（第 1/117 页）；点击订单 581475 显示明细 19 行、销量 352、£280.24，行金额可核对；商品搜索 `christmas` 返回 50 条并带“用作筛选”；交易明细页签加载 86,861 行、分页 25 行 |
| 05-imports.png / 06-import-result.png / 07-import-switched.png | 数据导入 | 通过文件选择器注入 `import_sample.csv`（模板 2 行）→ 上传成功 202 → 轮询到 succeeded → 显示质量统计（原始/保留/正向行=2、日期范围 2011-01-01~01-02、耗时 0.27s）→ 批次列表出现上传批次 → 点击“切换为当前批次”后顶部批次/覆盖范围变为上传批次 → 再点“切换”切回 original，顶部恢复 |
| 08-forecast.png / 09-forecast-result.png | 预测与回测 | 起点 2011-11-30、blend_rule：任务 5.3s 成功；WAPE 22.1%（基准 17.5%）、MAE 5,849.32、偏差 -3,445.45、有实际值 7/7；逐日表 7 行、预测/基准/实际折线、版本与批次标注、两条警告 |
| 10-backtest.png | 回测 | 2011-11-20~11-26、blend_rule：3.0s 成功；总 WAPE 19.7%（基准 21.8%）、MAE 4,602.24、偏差率 6.3%、评分目标 49；h=1..7 分步长 WAPE、高误差案例表；警告说明重叠起点非独立样本 |
| 11-inventory.png / 12-inventory-tables.png | 模拟库存 | 载入已有场景（2 商品，`simulated:true`）；入库 5 件后余额 100→105，流水、补货公式 `max(0, 提前期内需求+安全库存-当前库存-有效在途)`、建议补货、在途/流水表同步刷新；页面多处“模拟，不代表真实库存”声明 |
| 13-ai.png / 14-ai-brief.png | AI 助手 | 横幅明确“未配置真实大模型 / 未调用大模型 / provider=local-rule-planner / 可用工具 7 个”；提问“最近30天销量最高的商品是什么？”→ status=ok、llm_used=false、范围与批次、工具调用轨迹（product_ranking，参数与摘要）、回答引用工具数字；生成简报成功（provider=template，未调用大模型），显示预览与下载按钮 |
| 15-admin.png | 系统管理 | 用户列表（admin/operator/frontend_probe）、角色下拉、启停/重置密码；设置只读展示当前批次、AI 已配置=否、AI 密钥存在=否（无密钥回显）；新建账号 frontend_probe 成功并刷新列表 |
| 17-logout.png | 退出 | 顶部“退出登录”后回到登录页，显示“已退出登录”（info 样式），会话 Cookie 已撤销 |
| 18-operator.png | 运营角色 | operator 登录后侧栏无“系统管理”入口，角色徽标为 OPERATOR · 运营；看板等业务页正常 |

其它浏览器内验证：
- 未登录访问 `/api/options`/`/api/dashboard` 返回 401，页面引导登录；退出后重新加载走 `/api/auth/me` 401 → 登录页。
- 所有页面顶部固定“课程原型”标识；顶部显示当前用户+角色、批次 label+覆盖范围、退出登录。

## 4. 脚本补充验证（非浏览器点击，接口层）

- `GET /api/export/trend` 200，UTF-8 BOM + CSV 内容；`GET /api/orders/581475?start&end` 200 且明细与头部一致；不存在订单 404。
- `POST /api/imports` 上传同一 CSV 返回 202 `status:"duplicate"` 且带既有 batch_id。
- 预测/回测任务持久化轮询、`GET /api/jobs/{id}/export` 200 CSV。
- 库存固定样例：初始 100 + 入库 20 − 出库 30 = 90；同幂等键重复出库返回 `duplicate:true` 余额不变；补货候选 10、确认补货只登在途、到货入库后余额 +10（与契约 5.6 一致）。
- AI：`POST /api/ai/ask` 200（llm_used=false、带 tool_calls/range/batch/warnings）；`POST /api/ai/brief` 201 且 `GET /api/ai/briefs/{id}.md` 200 Markdown。
- 用户管理：`PATCH /api/users/3` 启停/改角色 200；停用自己 400（“不能停用当前登录的账号”）；重名创建 409（“用户名已存在”）——页面会把 error 显示出来。

## 5. 口径与红线落实

- 销售额统一标注 GBP 交易金额、**不等于利润**；页面口径条与看板“口径与批次”卡片常驻。
- 无记录日按 0 显示并标注 obs=0；不解释为“没有需求”。
- 模拟库存页常驻“模拟数据 · 不代表真实库存”，补货建议显示公式；应用未提供真实库存/成本/流量指标。
- 预测页标注“固定模型实验、不是经营收益预测”，WAPE/MAE 仅说明历史误差。
- AI 页区分“未配置真实大模型/未调用大模型/本地规则工具层”，不伪造自然语言结论；密钥不回显。
- 页面顶部固定“课程原型”标识；未实现/不存在的功能未出现在导航中。

## 6. 已知限制与未验证项（如实）

1. **浏览器环境限制**：agent 浏览器视口 1241px 宽（非 1280 整），截图按此宽度采集；CSS 在 1280px+ 为 4 列 KPI、桌面优先布局，窄屏逐级降为 2 列/单列（低于 1200/1100/850 的断点未逐档截图验证）。
2. **CSV 导出按钮**：导出接口已用脚本验证 200+BOM+文件名；浏览器内点击会触发下载到 agent 浏览器下载目录，未对落盘文件做逐字节对比（仅接口层验证）。
3. **原生 confirm 对话框**：用户“停用/启用”按钮带 `confirm()`，浏览器自动化未点击确认分支；同规则已用脚本 PATCH 验证（200/400/409）。
4. **模拟库存 UI 写操作覆盖**：入库流水与余额刷新已在浏览器实测；确认补货、到货入库按钮的点击未在浏览器逐一点击（接口层已按固定样例验证并通过），后续若按钮位置调整需复测。
5. **导入文件类型**：浏览器实测走 CSV（模板文件）；xlsx/xls 上传未在浏览器实测（后端解析由 data-api 负责，本前端 accept 已覆盖）。
6. **401 中途失效注入**：未构造“会话在操作中失效”的强制场景；`handleUnauthorized` 会在任意 API 401 时清空状态回登录页，退出登录路径已实测。
7. **数据依赖**：页面显示依赖后端返回字段与契约一致；本轮联调期间曾遇到 `GET /api/orders/{order_key}` 500（已修复）与早期 imports/ai 404，均已复测正常。若后端字段调整，需同步本前端。
8. **未实现但未展示**：无密码修改页、无 AI 真实大模型调用（未配置密钥）、无多用户并发/权限矩阵页面——均未在导航中出现，符合“不展示不存在的功能”。

## 7. 复现浏览器验收的最小步骤

1. 按第 1 节启动 8785 服务，读取 `.tmp/rt_fe/initial_admin.txt`。
2. 浏览器打开 `http://127.0.0.1:8785/` → 登录 admin。
3. 看板（默认筛选）→ 商品与订单（点订单 581475 看明细）→ 数据导入（上传 `模板 CSV`，切批次再切回）→ 预测（起点 2011-11-30，blend_rule）→ 回测（2011-11-20~11-26）→ 模拟库存（载入场景，入库 5）→ AI 助手（提问+简报）→ 系统管理（新建账号）→ 退出登录。
4. 用 operator 再登录一次，确认无“系统管理”入口。
