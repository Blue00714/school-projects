# M2（data-api）看板迁移 / 订单聚合 / 导入与批次 验收记录

- 负责人：data-api（M2）
- 时间：2026-10-29 15:00—15:12（Asia/Shanghai, UTC+08:00）
- 会话工作区：`C:\Users\Administrator\11111\数据\学校项目`
- 系统目录：`C:\Users\Administrator\11111\数据\学校项目\电商运营系统`
- 依据：`电商运营系统\API契约.md` 第 1/2/3/4/5.3/5.4/6 节
- 结论：**M2 两项任务自测通过**。看板/商品/交易迁移无回归；订单聚合与看板口径一致；导入→duplicate→切换→切回全链路通过；既有 16 项服务测试 16/16 通过。遗留项见第 8 节（`.xls` 依赖、隔离环境路径形态、订单明细缺陷修复记录）。

## 1. 本次改动文件（仅本人作用域）

| 文件 | 动作 | 说明 |
|---|---|---|
| `api/routers/analytics.py` | 重写 | `/products`、`/dashboard`、`/transactions`、`/orders`、`/orders/{order_key}`、`/export/trend|orders|transactions`；统一按当前批次取数 |
| `api/routers/imports.py` | 重写 | `POST /imports`、`/imports/jobs/{id}`、`/imports/batches`、`/imports/batches/{id}/activate`、`/imports/template.csv` |
| `api/services/importer.py` | 新建 | 上传落盘、sha256 去重、后台线程导入、质量 issues、批次登记 |
| `api/services/orders.py` | 新建 | 订单聚合 SQL、订单明细、CSV（BOM/注入中和/流式导出） |
| `services/data.py` | **增量兼容** | 抽出 `clean_frames()` / `build_from_frames()` 供导入复用；`prepare()` 行为不变（见第 7 节） |
| 本文件 | 新建 | 验收记录 |

未改动 `api/config.py`、`api/db.py`、`api/deps.py`、`api/errors.py`、`api/main.py`、`api/services/batches.py`、`web/`、其它队友 router、`runtime/retail.sqlite3`、原始 Excel。

## 2. 隔离运行环境与命令

```powershell
$root = "C:\Users\Administrator\11111\数据\学校项目"
$env:ECE_RUNTIME_DIR  = "$root\.tmp\rt_data"
$env:ECE_ORIGINAL_DB  = "$root\电商运营系统\runtime\retail.sqlite3"   # 只读读取
$env:ECE_APP_DB       = "$root\.tmp\rt_data\app.sqlite3"
$env:ECE_ADMIN_PASSWORD = "AdminTest123"
$env:ECE_OPERATOR_PASSWORD = "OperTest123"
cd "$root\电商运营系统"; python run.py --port 8782
```

- Python 3.12.10；FastAPI 由 `电商运营系统\.pydeps` 自动加载；pandas 3.0.5。
- 隔离运行时：`C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_data`（批次库、导入源文件、app 库全部在此，**不写**项目 `runtime\`）。
- 探测脚本与原始输出（临时证据，未纳入交付）：`.tmp\m2_data_api\probe1.py|probe2.py|probe3.py|probe4.py` 及同名 `.json`；样例 `sample_upload.xlsx`、`sample_upload.csv`、`large_upload.csv`。
- 每次重跑导入用例前清空 `.tmp\rt_data` 并重启服务，保证 `duplicate` 语义是“同进程历史批次”而不是上一轮残留。

## 3. 任务 A：看板/商品/交易迁移 + 订单聚合 + 导出

### 3.1 原始批次看板（对照值）

`GET /api/dashboard?start=2011-11-10&end=2011-12-09` → **200**，耗时 **0.266 s**

| 指标 | 期望（历史对照） | 实测 |
|---|---|---|
| totals.quantity | 851,403 | **851,403** |
| totals.revenue | 1,694,068.71 | **1,694,068.71** |
| totals.orders | 2,913 | **2,913** |
| totals.lines | 86,861 | **86,861** |
| totals.products / amount_rows | — | 2,930 / 86,861 |
| trend 天数 | 30 | 30 |
| warnings | 空 | `[]` |
| batch | — | `{id:"original", label:"原始数据 2009-12-01~2011-12-09", kind:"original"}` |

既有字段（`filters/coverage/totals/previous/changes/trend/ranking/countries/weekdays/quality/warnings/policy_version`）全部保留，仅追加 `batch`。

### 3.2 订单列表 `GET /api/orders`

- `?start=2011-11-10&end=2011-12-09&page=1&page_size=200` → **200**；`total=2913`（= 看板 orders）；单页 200 行，共 15 页，**15 页合计耗时 2.015 s**。
- 逐页汇总：`sum(quantity)=851,403`（= 看板）；`sum(revenue)=1,694,068.71`（与看板差值 **0.0**）；唯一 `order_key` 2,913 个。
- 行结构：`order_key,date,first_time,last_time,customer_id,country,lines,quantity,revenue,product_codes`（首行 `581475`：2011-12-09、19 行、qty 352、revenue 280.24、`product_codes` 5 个）。
- `product_codes` 每单 ≤5（按数量降序取前 5，SQL 子查询仅针对当前页订单）。
- 排序 `date desc, order_key`；`total = count(distinct order_key)`；金额 `sum(amount_units)/10000.0`。
- 筛选与看板逐项对齐：`product=84077` → 订单 84 = 看板 orders 84；`country=France` → 68 = 68。
- `q` 仅作用于订单列表（订单号或客户号模糊匹配，通配符已转义），不改变看板：`q=581475` → 1 单，`q=13069` → 2 单。
- 默认分页 `page=1&page_size=25`；非法参数统一 400：`page=0`/`page_size=201`→“页码至少1，单页1至200行”、`page=abc`→“分页参数应为整数”、`start>end`、非法日期、>4 年跨度均为 400 `bad_request`。

### 3.3 订单明细 `GET /api/orders/{order_key}`

`?start=2011-11-10&end=2011-12-09` → **200**：头部 `lines=19,quantity=352,revenue=280.24`；明细 19 行相加 `quantity=352`、`revenue=280.24`，**与头部一致**。明细字段：`invoice,stock_code,description,quantity,unit_price,revenue,invoice_time,customer_id,country,source_row,sheet`。
订单存在但不在筛选范围 → **404**（`not_found`）；不存在 → 404。

> 缺陷记录：首版此处 500（`sqlite3.ProgrammingError: bindings 2 vs 3`）。原因是明细 SQL 带 `and order_key=?` 而汇总头部 SQL 漏了同一占位符；已由 frontend 联调发现并修复（`api/services/orders.py:157-176`），修复后 200 且数值自洽。

### 3.4 导出（UTF-8 BOM / 公式注入中和 / Content-Disposition）

所有导出的响应头一致：`Content-Type: text/csv; charset=utf-8`，`Content-Disposition: attachment; filename="<name>.csv"`，首字节 `EF BB BF`。

| 端点 | 状态 | 行数 | 耗时 | 文件名/表头 |
|---|---|---|---|---|
| `/api/export/trend` | 200 | 31（表头+30 天） | — | `sales-trend.csv`，`date,quantity,revenue,orders,observed` |
| `/api/export/orders` | 200 | 2,914（表头+2,913 单） | 0.141 s | `orders.csv`，`order_key,date,first_time,customer_id,country,lines,quantity,revenue` |
| `/api/export/transactions` | 200 | 86,862（表头+86,861 行） | 1.218 s | `transactions.csv`，`invoice,stock_code,description,quantity,unit_price,revenue,invoice_time,customer_id,country,sheet,source_row`；10,009,829 字节（流式分块） |

CSV 加固（上传批次实测）：长编号 `123456789012345678` 原样保留为文本；`=SUM(1,2)` 输出为 `'=SUM(1,2)`；负数单号 `-700000` 输出为 `'-700000`（公式中和）。样例行：`581587,22138,BAKING SET 9 PIECE RETROSPOT,3,4.95,14.85,2011-12-09 12:50:00,12680,France,Year 2010-2011,541910`。

### 3.5 其它迁移端点

- `GET /api/products?q=A` → **200**，裸数组，50 条（≤50），元素 `{stock_code,name,quantity}`。
- `GET /api/transactions?...&page=1&page_size=5` → **200**，`total=86,861`（= 看板 lines），既有字段不变 + `batch`。

## 4. 任务 B：导入与批次

### 4.1 xlsx 导入（20 行 / 2 sheet：主表 15 行 + 次表 5 行，其中 3 行为跨表副本）

`POST /api/imports`（multipart `file`，label=`M2 样例导入`）→ **202** `{job_id,status:"queued"}`；轮询到 `succeeded` 共用 **0.58 s**（含建库）。

质量报告（job.report）与“按源行独立重算”的期望值**逐项相等**：

| 字段 | 期望 | 实测 |
|---|---|---|
| raw_rows | 20 | 20 |
| cross_sheet_copies_excluded | 3 | 3 |
| retained_rows | 17 | 17 |
| positive_sale_rows | 13 | 13 |
| cancelled_rows / invalid_dates | 1 / 1 | 1 / 1 |
| nonpositive_quantity_rows | 3 | 3 |
| sale_rows_without_valid_price | 1 | 1 |
| date_min~date_max | 2011-01-03~2011-01-16 | 同 |
| source_sha256 / source_kind | 文件哈希 / upload | 一致 / upload |

- `issues` 9 条（5 warning + 4 info），覆盖非法日期、非正数量、取消行、单价缺失、重复候选，消息格式“工作表 SheetA 第 14 行：…”，可定位。
- **不自动切换**：导入成功后在 `GET /api/imports/batches` 中 `active_batch_id` 仍为 `original`。
- 批次库落盘：`<ECE_RUNTIME_DIR>\batches\<batch_id>\data.sqlite3` + `data_manifest.json`（45,056 字节），app 库 `batches` 行 `kind=upload,status=ready,is_active=0`。
- **重复导入（同 sha256）**：再次 POST → **202** `{status:"duplicate", batch_id:<既有>}`，批次总数保持 2，不新建数据；该 duplicate 也写了一条 `import_jobs` 记录（`GET /jobs/{id}` 可查 `status=duplicate`）。
- 同字节、不同文件名同样识别为 duplicate（按 sha256，不按文件名）。

### 4.2 切换批次并验证看板随批次变化

`POST /api/imports/batches/<new_id>/activate` → **200** `{batch:{...is_active:true}, previous_batch_id:"original"}`。

切换后 `GET /api/dashboard`（默认区间=批次 date_max 往前 29 天）与 `GET /api/orders`：
quantity **42** / revenue **358.87** / orders **12** / lines 13，与第 4.1 节期望一致；订单列表 `total=12`，`batch.id` 为新批次。

`POST /api/imports/batches/original/activate` 切回 → 看板恢复 **851,403 / 1,694,068.71 / 2,913 / 86,861**。

### 4.3 CSV 导入与失败路径

| 场景 | 结果 |
|---|---|
| 6 行 CSV 导入（4 正向 + 1 取消 + 1 非法日期） | **202** → `succeeded`（1.14 s），`raw_rows=6, positive_sale_rows=4, cancelled_rows=1, invalid_dates=1`，新建批次 |
| 同 CSV 重复导入 | **202** `status:"duplicate"` + 既有 `batch_id` |
| 缺列 CSV（仅 Invoice/StockCode/Quantity） | **202** → `failed`，`error="缺少必要字段：CSV 缺少列 Description, InvoiceDate, Price, Customer ID, Country。示例表头见 /api/imports/template.csv"`；`issues` 含 1 条 error；`batch=null`；未建库 |
| 非法扩展名 `.txt` | **400** `bad_request`（仅支持 .xlsx/.xls/.csv） |
| 空文件 | **400**“上传文件为空” |
| 21 MB 文件 | **400**“文件超过 20MB 上限” |
| 伪装成 `.xls` 的非 Excel 字节 | 202 → `failed`，`error="Excel 文件无法解析：…"`，无批次 |
| `POST /api/imports` 缺 `file` 字段 | **400** |
| 未登录访问任一数据端点（含 POST /imports） | **401** `unauthorized` |
| 未知 job_id / activate 不存在批次 | **404**；activate 非法批次（status≠ready）→ **400** “该批次尚未就绪，不能切换” |

- `GET /api/imports/template.csv` → **200**，`import-template.csv`，3 行（表头+2 样例），表头 `Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country`，带 BOM。
- `GET /api/imports/batches` → `{batches:[batch_public...],active_batch_id}`；最终隔离环境 3 个批次（original + xlsx + csv），active=original。

### 4.4 导入期间当前批次照常可查（并发）

导入 120,002 行 CSV（约 40000 订单）：POST 立即 **202**，job 状态序列 `queued → running(×多) → succeeded`，文件 manifest 统计耗时 **7.58 s**，端到端观察 7.69 s。

导入进行中每轮同时请求原始批次看板与订单列表：

| job 状态 | 看板耗时 | 看板数值 | 订单列表耗时 | 订单 total |
|---|---|---|---|---|
| queued | 2.797 s | 851403 / 1694068.71 / 2913 | 0.625 s | 2913 |
| running | 0.516 s | 同 | 0.171 s | 2913 |
| running | 0.985 s | 同 | 0.203 s | 2913 |
| running | 0.484 s | 同 | — | 2913 |
| running | 0.469 s | 同 | — | 2913 |

结论：导入写新库期间当前批次查询不受影响（不同 sqlite 文件；导入线程串行、不阻塞读）。

导入完成后：新批次库 `data.sqlite3` 25,780,224 字节；表 `transactions/metadata/forecast_runs`；含 `ix_transactions_order` 索引；`metadata.manifest` == `data_manifest.json` == `job.report`；看板 240,003 / 827,995.70 / 40,002 / 120,002 与独立期望一致；导出订单 40,003 行、交易 120,003 行（BOM、长编号、公式中和均通过）；切回 original 后基线恢复。

## 5. 既有 16 项服务测试

```
cd C:\Users\Administrator\11111\数据\学校项目\03_测试与验证\系统测试
python -m unittest discover -s tests -p test_services.py -v
```

```
Ran 16 tests in 6.766s
OK      exit=0
```

在 `services/data.py` 增量重构后与最终状态各跑一次，均 16/16 通过（`ForecastTests` 8 项、`DataAndAnalyticsTests` 8 项，含 `prepare()` 缓存/跨表副本/订单身份冲突/CSV 注入中和用例）。

## 6. 只读与写入范围核对

| 对象 | 测试前 | 测试后 | 结论 |
|---|---|---|---|
| `电商运营系统\runtime\retail.sqlite3` SHA-256 | `033f4aa2972771fddd95045f6f2de0a63af1d9c744090778a76c8fb803ef61bf` | 同值 | **未被修改**（与 M0 基线一致） |
| 同上 mtime / 大小 | 2026-10-21T18:44:48 / 284,557,312 | 同值 | 未变 |
| `20261014_电商数据与页面选型\online_retail_II.xlsx` SHA-256 | `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980` | 同值 | **未被修改**；导入只读副本 |
| `电商运营系统\runtime\batches\` | 空目录 | 仍为空 | 上传批次只写隔离 `ECE_RUNTIME_DIR` |

写入仅发生在：`.tmp\rt_data\`（app/批次库/导入源文件）、`.tmp\m2_data_api\`（探测脚本与样例）、本记录文件、以及 `test_tmp\`（既有测试自身约定）。

## 7. `services/data.py` 增量修改说明（兼容性）

- 新增 `clean_frames(sheets, progress)`：把原 `prepare()` 中的清洗/口径逻辑（跨表副本 nth-occurrence 匹配、sale/amount_eligible/amount_units、source_row）原样抽出，纯 DataFrame、无 I/O。
- 新增 `build_from_frames(sheets, db, *, source_file, source_sha256, source_kind, progress, begun)`：写库 + 索引 + metadata/manifest + 订单身份冲突校验 + side `data_manifest.json`，返回 `(manifest, result)`。
- `prepare()` 改为“读 Excel → build_from_frames”，签名、返回、缓存迁移分支、manifest 字段、进度输出、elapsed 起始点均保持不变；新增 `AUX_SQL` 中 `ix_transactions_order` 索引仅提升订单聚合性能（不改口径）。既有 16 项测试复跑通过即为兼容性证据。

## 8. 遗留 / 偏差（如实记录）

1. **`.xls`（旧二进制格式）依赖缺失**：环境未安装 `xlrd`（`.pydeps` 也没有），真实 `.xls` 会进入 failed 并提示“读取 .xls 文件缺少依赖（…）；请另存为 .xlsx 后重试”。契约 5.4 要求支持 `.xls`；补 `xlrd` 需要改 `requirements.txt` 与 `.pydeps`（Lead 作用域），**本人未擅自修改**，请 Lead 决定是否补齐。`.xlsx`/`.csv` 已实测可用。
2. **隔离环境批次路径为绝对路径属预期**：`batches.register_batch` 用 `db.stored_db_path()`，只有路径位于 `APP_ROOT` 内才记录相对路径。本次 `ECE_RUNTIME_DIR` 在 `.tmp`（APP_ROOT 外），故隔离批次记录绝对路径；生产默认 `runtime` 在系统目录内，实测 `stored_db_path(APP_ROOT/runtime/batches/demo/data.sqlite3) = "runtime/batches/demo/data.sqlite3"`（相对）。qa 用临时 `ECE_RUNTIME_DIR` 时同样会看到绝对路径，这是设计回退而非缺陷。
3. **订单明细/导出的筛选耦合**：明细与导出都受同一 `start/end/product/country` 约束（默认 end=date_max、start=end-29 天）。从订单列表点进明细时必须带上当前筛选，否则范围不同会得到 404。
4. **取消口径提示**：订单号以 `C` 开头的行按既有口径计为取消（`services/data.py: cancelled = invoice 以 C 开头`）。自测第一版样例把单号写成 `CSV300001`，被正确判为取消导致“无正向销售”，属样例设计问题、非代码缺陷；用户导入时单号勿以 C 开头。
5. **导入串行**：同一时刻只处理一个导入（模块级锁），job 状态 `queued` 会等待前一个完成；契约未要求并发导入，如需并行需另行评估 sqlite 写并发。
6. **上传源文件保留**：`ECE_RUNTIME_DIR\imports\<job_id>\source.<ext>` 保留原始副本便于追溯，API 不暴露；如需清理策略请 Lead 定。
7. **未做 dashboard 额外缓存**：直接复用既有 `services.analytics`，30 天窗口实测 0.27 s，暂未加缓存；批次切换后无需失效逻辑（每次请求按当前批次取数）。
8. 证据文件（`probe*.json`、样例文件）在 `.tmp\m2_data_api\`，不在交付目录；如需纳入 `runs\20261029_prototype\` 请 Lead/qa 复制（该目录写权限属 qa）。

## 9. 复现命令（最小）

```powershell
# 1) 启动隔离服务（见第 2 节）
# 2) 原始对照 + 订单 + 导出 + 错误码
cd C:\Users\Administrator\11111\数据\学校项目\.tmp\m2_data_api; python probe1.py
# 3) xlsx 导入→duplicate→activate→看板变化→切回 + CSV + 失败路径（先清空 .tmp\rt_data 并重启）
python probe2.py
# 4) 12 万行导入并发 + 批次库结构 + CSV 加固 + 只读哈希
python probe3.py
# 5) 鉴权/404/400/非就绪批次
python probe4.py
# 6) 既有 16 项测试
cd C:\Users\Administrator\11111\数据\学校项目\03_测试与验证\系统测试
python -m unittest discover -s tests -p test_services.py -v
```
