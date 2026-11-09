# M3/M4 预测任务与模拟库存 — 自测记录（20261029 原型）

- 负责人：forecast-inventory（M3/M4）
- 运行时间：2026-10-29 15:05–15:06（Asia/Shanghai；服务端日志时间戳为 UTC 07:05–07:06）
- 契约依据：`电商运营系统/API契约.md` 第 1/2/3/5.5/5.6/6 节
- 环境：Windows 10 + Python 3.12.10（系统解释器，无 venv）；FastAPI/uvicorn 由 `api/__init__.py` 自动加入 `.pydeps`；pandas 3.0.5、lightgbm 4.6.0；原始批次库 `runtime/retail.sqlite3`（只读，实测未被修改：大小 284,557,312 字节、mtime 2026-10-21 18:44:48）
- 结论：**自测 50/50 项通过（FAILURES=none）**；预测任务、回测任务、CSV 导出、重启后仍可查、批次绑定、库存固定验收样例全部按契约工作

## 1. 交付文件（仅本模块写作用域内）

| 文件 | 说明 |
|---|---|
| `电商运营系统/api/services/jobs.py`（新建） | 预测/回测任务持久化与后台线程执行、任务查询、CSV 导出 |
| `电商运营系统/api/routers/forecast.py` | `/api/forecast`、`/api/backtest`、`/api/jobs/{id}`、`/api/jobs/{id}/export` |
| `电商运营系统/api/services/inventory.py`（新建） | 模拟库存场景/商品/流水/在途/补货计算 |
| `电商运营系统/api/routers/inventory.py` | `/api/inventory/**` 共 10 个端点 |
| 本文件 | 自测记录 |

未修改 `services/forecasting.py`（冻结算法）、Lead 核心文件、`web/`、其它队友文件、原始数据与 `runtime/retail.sqlite3`。自测临时驱动与日志放在工作区 `.tmp/`（见第 8 节），未写入 qa 的证据目录。

## 2. 复现命令（真实执行）

自测驱动会自行启动/停止正式入口 `run.py`（隔离 runtime），并做一次重启验证：

```powershell
cd "C:\Users\Administrator\11111\数据\学校项目\电商运营系统"
$env:PYTHONIOENCODING = "utf-8"

# 1) 库存服务层固定样例（无需 HTTP、无需登录）
python "..\.tmp\fc_inventory_service_probe.py"

# 2) 全链路 HTTP 自测：启动 run.py --port 8783 → 50 项断言 → 重启服务 → 再断言
python "..\.tmp\fc_http_selftest.py"
```

驱动内部使用的隔离环境（与契约要求一致，`ECE_ORIGINAL_DB` 指向真实只读批次库，`ECE_APP_DB` 指向隔离状态库）：

```powershell
$env:ECE_RUNTIME_DIR="C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_fc"
$env:ECE_ORIGINAL_DB="C:\Users\Administrator\11111\数据\学校项目\电商运营系统\runtime\retail.sqlite3"
$env:ECE_APP_DB="C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_fc\app.sqlite3"
$env:ECE_ADMIN_PASSWORD="Selftest-Admin-123"
cd C:\Users\Administrator\11111\数据\学校项目\电商运营系统
python run.py --port 8783
```

说明：并行开发阶段 M1 登录路由尚未合并完成，自测用「直接写入隔离状态库的会话行」取得 Cookie（`api/security` 的 token 摘要），未改任何认证代码；随机端口 8783 与 Lead 验收脚本的 8790 不冲突。

## 3. 任务 A：预测/回测任务（契约 5.5）

### 3.1 未来 7 天预测（原始批次，默认参数）

命令等价于 `POST /api/forecast {}`（自测实际发送 `{}`），返回：

```
202 {"job_id":"5bde6243a10345aa80bd8738dca00b0c","status":"queued","batch_id":"original"}
```

任务结果（`GET /api/jobs/5bde6243...`，本地日志 15:06:00）：

| 项目 | 实测 |
|---|---|
| status | `succeeded`，error=null |
| created/started/finished | 07:05:58+00:00 / 07:05:58+00:00 / 07:06:00+00:00 |
| elapsed_seconds | 1.53 |
| origin / actual_method / requested_method | 2011-12-09 / blend_rule / blend_rule |
| model_version | `daily-v1-hw-fixed-lgbm400` |
| data_policy_version | `cross-sheet-multiset-v1-sales-v2-invoice-order` |
| batch / result.batch_id | `original` / `original` |
| training_rows | 4543 |
| rows | **7 行**（2011-12-10…2011-12-16），每条含 `date,h,prediction,baseline,actual,abs_error` |
| actual | **7 行全部 `null`**（未来日期无实际值） |
| metrics / baseline_metrics | 均含 `n,MAE,RMSE,WAPE,BIAS,BIAS_PCT` 字段；此时 `n=0`，WAPE 等为 `null`（无实际值可评分，属预期） |
| closed_weekdays / warnings | `[5]`；2 条口径提示（无记录日补 0、零销量星期为候选规则） |

7 行明细（`prediction` / `baseline`，原始值取 `jobs.result_json`）：

| date | h | prediction | baseline | actual |
|---|---|---|---|---|
| 2011-12-10（周六） | 1 | 0.0 | 0.0 | null |
| 2011-12-11 | 2 | 29657.7369 | 15960.25 | null |
| 2011-12-12 | 3 | 47294.1835 | 36893.75 | null |
| 2011-12-13 | 4 | 45892.3078 | 32086.5 | null |
| 2011-12-14 | 5 | 48432.8855 | 35769.0 | null |
| 2011-12-15 | 6 | 47049.0925 | 30336.25 | null |
| 2011-12-16 | 7 | 54400.5372 | 44729.5 | null |

> 2011-12-10 预测为 0 来自冻结算法的 `closed_weekdays=[5]`（周六零销量候选规则），不是"确认停业"，结果 warnings 已注明。

### 3.2 覆盖窗口内起点预测（验证 WAPE 可计算）

`POST /api/forecast {"origin":"2011-11-01","model":"blend_rule"}` → `succeeded`，elapsed 0.44s，`n=7`：

- 组合预测：MAE 6920.8375、RMSE 8422.1572、WAPE **26.90%**、BIAS −6920.8375
- 强基准：MAE 4814.2857、RMSE 6634.1251、WAPE **18.72%**、BIAS −4814.2857

如实记录：**该单一起点窗口内基准优于组合**；这与 README 记录的历史 364 条组合对照（组合 23.34% / 基准 24.54%）不可直接比较（窗口、起点数、方法组合均不同，且重叠起点不是独立样本）。算法本身未改动。

### 3.3 小窗口回测

`POST /api/backtest {"start":"2011-10-01","end":"2011-10-07"}`（7 个日历日，窗口校验 1—56 天）→ 202，任务：

| 项目 | 实测 |
|---|---|
| status / elapsed_seconds | succeeded / 4.3 |
| origins_attempted / complete_origins | 7 / 7 |
| forecast_count / **scored_count** | 49 / **49（>0）** |
| metrics（可评分 49 条） | MAE 8097.2403、RMSE 10697.5444、WAPE **36.25%**、BIAS 45.4121 |
| baseline_metrics | MAE 7895.7398、RMSE 10398.9173、WAPE 35.34%、BIAS −2535.6480 |
| 结构 | `origin_results` 7 个起点，各含 7 行 + 分起点指标 + 实际方法；顶层含 `by_horizon`、`worst_cases`、warnings |

### 3.4 CSV 导出

`GET /api/jobs/{id}/export`：

- HTTP 200，`Content-Type: text/csv; charset=utf-8`，`Content-Disposition: attachment; filename="forecast-forecast-5bde6243.csv"`
- 文件以 **UTF-8 BOM**（`EF BB BF`）开头
- 表头逐字一致：`origin,date,h,prediction,baseline,actual,abs_error,model_version,data_policy_version,batch_id,actual_method`
- 预测任务：8 行（表头 + 7 行）；`actual` 为空；`origin=2011-12-09`；`batch_id=original`；`actual_method=blend_rule`
- 回测任务：**50 行**（表头 + 49 行），每行 `origin` 与 `actual_method` 均非空（例：`2011-10-01,2011-10-02,1,16586.64663,15152,8385,8201.646632,daily-v1-hw-fixed-lgbm400,cross-sheet-multiset-v1-sales-v2-invoice-order,original,blend_rule`）
- 未成功任务导出 → 400（契约要求）；不存在任务 → 404
- 文本单元格做公式注入中和（`=+-@` 开头加 `'`）

### 3.5 批次绑定

自测在隔离库中登记并激活一个"副本批次"`selftest-snapshot`（同一数据文件、不同 `batch_id`/policy），验证：

| 场景 | 实测 |
|---|---|
| 切换批次后查询旧任务 | `job.batch.id=original`，`result.batch_id=original`，7 行结果不变 |
| 切换后新提交任务 | 202 返回 `batch_id=selftest-snapshot`；任务成功，`result.batch_id=selftest-snapshot`、`data_policy_version=selftest-policy` |

实现方式：任务行写入时固化 `batch_id`，后台线程按 `batch_id` 取批次库（不取"当前批次"），因此切换批次不影响运行中/已存结果。

### 3.6 重启后仍可查

停止服务 → 重新启动同一隔离 runtime → 用**同一 Cookie**再查：

- `GET /api/jobs/5bde6243...` → 200，`status=succeeded`，`result.rows` 仍为 7 行，`result.batch_id=original`
- 同一任务 CSV 重新导出 → 200，7 行数据
- 库存场景余额 100、3 条流水同样保留

依据：`main.py` 启动时把遗留 `queued/running` 标记 failed，`succeeded` 的 `jobs.result_json` 原样保留（本次实测未触发遗留任务场景）。

### 3.7 参数与错误契约（HTTP 实测）

| 请求 | 实测 |
|---|---|
| `POST /api/forecast {"model":"no_such_method"}` | 400 `{"error":"未知预测方法，可选：blend_rule、lgbm、holt_winters、seasonal_week4、naive_week","code":"bad_request"}` |
| `POST /api/forecast {"origin":"2012-06-01"}` | 400 `预测起点必须在当前批次数据覆盖范围 2009-12-01～2011-12-09 内` |
| `POST /api/backtest {"start":"2011-10-01","end":"2011-12-01"}`（62 天） | 400 `回测窗口须为 1 至 56 个日历日` |
| `GET /api/jobs/does-not-exist` | 404 `{"error":"任务不存在","code":"not_found"}` |
| 未登录 `GET /api/jobs/{id}` | 401 `{"error":"未登录或登录已失效","code":"unauthorized"}` |

## 4. 任务 B：模拟库存（契约 5.6）

### 4.1 固定验收样例（HTTP 逐步实测）

场景：`POST /api/inventory/scenarios {"name":"HTTP验收场景","note":"固定样例"}`（**不带 stock_codes**，契约允许空数组）→ 201 `{scenario:{id,...},simulated:true}`；随后 `PUT .../items/C001`（**C001 不在原始批次中也创建，Lead 补丁的 upsert 语义**）：

| 步骤 | 请求 | 状态 | 关键实测 |
|---|---|---|---|
| 1 | `PUT .../items/C001 {"name":"Delta Box","initial_qty":100,"lead_time_days":8,"safety_stock":30,"daily_demand":10}` | 200 | item.balance=**100**，name=Delta Box，lead=8，daily=10，simulated=true |
| 2 | `PUT .../items/C002 {"initial_qty":5}` | 200 | name 缺省回退为 **`C002`**，balance=5（upsert 创建） |
| 3 | `POST .../movements {"stock_code":"C001","kind":"in","qty":20,"idempotency_key":"http-in-1"}` | 201 | balance_after=120，item.balance=120 |
| 4 | `POST .../movements {"stock_code":"C001","kind":"out","qty":30,"idempotency_key":"http-out-1"}` | 201 | balance_after=**90**，item.balance=90 |
| 5 | 同 key 重复出库（qty 30, key `http-out-1`） | **200** | `duplicate:true`，返回**同一流水 id** `73dd5e8c…`，余额仍 **90** |
| 6 | `GET .../movements` | 200 | 仍 **2 条**（未重复记账），时间倒序，simulated=true |
| 7 | `POST .../movements {"kind":"out","qty":91,"idempotency_key":"http-out-91"}` | **400** | `{"error":"出库数量超过当前余额：当前余额 90，请求出库 91","code":"bad_request"}` |
| 8 | `POST .../replenishment/confirm {"stock_code":"C001","qty":10,"expected_date":"2011-12-15"}` | 201 | `in_transit.status=in_transit`，**item.balance 仍 90**（只登记在途，不加库存） |
| 9 | `GET .../replenishment` | 200 | `formula="max(0, 提前期内场景需求 + 安全库存 - 当前库存 - 有效在途量)"`；`lead_time_demand=8×10=80`、`current_qty=90`、`effective_in_transit=10`、`safety_stock=30`、**`candidate=10`** |
| 10 | `POST .../in-transit/{iid}/receive` | 200 | 同一事务写 `kind="in"` 流水 qty=10 → balance **100**，在途 `status=received` |
| 11 | `GET .../in-transit` | 200 | 1 条、状态 received、simulated=true |
| 12 | `GET .../items` | 200 | 每条含 `stock_code,name,initial_qty,lead_time_days,safety_stock,daily_demand,balance,in_transit_effective,in_transit_total,replenishment_candidate` |
| 13 | `GET .../scenarios` | 200 | `scenarios:[{id,name,note,created_at,item_count}]`，item_count=2，simulated=true |
| 14 | `POST .../movements {"qty":0}` / `PUT lead_time_days=400` / `GET movements?limit=999` | 400/400/400 | 参数校验统一 400 契约 |
| 15 | `GET /api/inventory/scenarios/not-a-scenario/items` | 404 | `模拟库存场景不存在` |

补充：`POST /api/inventory/scenarios {"name":"批次商品场景","stock_codes":["85123A"]}` → 201，item_count=1，商品名称从当前批次回填为 `WHITE HANGING HEART T-LIGHT HOLDER`（名称回填尽力而为，编号不存在时不影响创建）。

### 4.2 持久化与"模拟"边界

- 重启服务后用同一 Cookie：场景仍可查，C001 `balance=100`，流水 **3 条**（in 20 / out 30 / 在途到货 10）
- 余额完全由「初始库存 + 入库 − 出库」得出，**没有任何一处把历史交易自动扣减为库存**；`inventory_items` 的人工参数也不会被预测结果写回
- 所有库存响应（场景列表/创建、items、movements、replenishment、confirm、in-transit、receive、PUT item）都带 `"simulated": true`

## 5. 设计要点与契约逐条对应

### 5.5 预测/回测任务

- `POST /api/forecast`：校验 `model ∈ METHODS`（400）、`origin` 默认 `manifest.date_max`、必须在批次覆盖范围（400）；插入 `jobs(status='queued', params_json, batch_id=当前批次, created_by, created_at)` 后启动后台线程，**202** 返回 `{job_id,status:"queued",batch_id}`。
- `POST /api/backtest`：`start/end` 必须合法日期、`start<=end`、窗口 1—56 日历日（`(end-start).days+1`）、在批次覆盖范围内；同样任务化。
- 后台线程：`status='running'` + `started_at` → 按 `batch_id` 取批次库 → `store.series()` → **直接调用冻结的 `services.forecasting.forecast/backtest`**（结果结构原样保留）→ 追加 `batch_id`、`data_policy_version` → 写 `result_json`、`status='succeeded'`、`finished_at`、`elapsed_seconds`；异常捕获后 `status='failed'` + `error`（不吞异常，前端可见）。
- 线程安全：每个线程每次操作都用 `api.db.connect()` 新建短连接（30s timeout / 10s busy_timeout），不共享连接；同一时刻排队/运行任务上限 8 个（额外保护，避免无限线程）；`result_json` 为单条 UPDATE，避免长事务。
- `GET /api/jobs/{job_id}`：返回 `{job:{id,kind,status,created_at,started_at,finished_at,elapsed_seconds,error,params,batch,result?}}`；`batch` 为创建时批次的公开结构；`result` 仅在已写入时出现。
- `GET /api/jobs/{job_id}/export`：未成功 400；统一把「预测的 `rows`」与「回测的 `origin_results[].rows`」摊平成同一套列；UTF-8 BOM + `text/csv; charset=utf-8` + 附件文件名 + 公式注入中和。
- 可选能力（同参数缓存任务）未实现——契约标注为可选；启动恢复由 `api/main.py` 负责（本模块未改）。

### 5.6 模拟库存

- 表结构直接使用 Lead 的 `inventory_*` 四张表，**未改 schema**。
- 余额：`initial_qty + Σ(kind='in') − Σ(kind='out')`；流水与余额同一事务（写流水时先 `BEGIN IMMEDIATE` 取写锁，再读余额、校验、`INSERT` 并落 `balance_after`，避免并发丢更新）；幂等键冲突走 `inventory_movements(scenario_id,idempotency_key)` 唯一索引，重复提交返回 200 `{duplicate:true,...}` 且不重复记账（并发同 key 触发 `IntegrityError` 时也回读既有流水）。
- 出库余额不足：400，错误文案含当前余额与请求数量。
- `GET movements`：`limit` 默认 200、最大 500，超出 400；`ORDER BY created_at DESC, rowid DESC`。
- 补货：`lead_time_demand = lead_time_days × daily_demand`；`effective_in_transit = Σ status='in_transit' 且 expected_date ≤ 今天+lead_time_days`（今天取本机 Asia/Shanghai 日期；逾期在途按有效计）；`candidate = max(0, lead_time_demand + safety_stock − balance − effective_in_transit)`。
- `replenishment/confirm`：只插入 `inventory_in_transit(status='in_transit')`，不改余额；`in-transit/{iid}/receive`：同一事务写 `kind='in'` 流水 + 置 `status='received'`，重复到货 400。
- 数值校验：`initial_qty/safety_stock/daily_demand/qty ≥ 0`（qty 必须 >0）、`lead_time_days` 为 **0—365 整数**、`name` 文本上限、`stock_codes` 去重且场景商品上限 500；所有错误统一 `{error,code}` 400/404。

## 6. 与 Lead 验收脚本（`lead_flow_acceptance.py` 第 21—33 步）口径核对

| Lead 断言 | 本模块实测 |
|---|---|
| 21 预测提交 202 + job_id | ✔ 202 `{job_id,status:"queued",batch_id}` |
| 22 succeeded、7 行、`actual` 全 null、含 actual_method/requested_method/batch_id/warnings/origin | ✔ 完全一致 |
| 23 CSV 可导出、`text/csv`、8 行 | ✔ 200、8 行、BOM、表头一致 |
| 24/25 回测 2011-11-01…03 成功、`scored_count>0`、`metrics.WAPE` 非空 | ✔ 同构（本次另测 2011-10-01…07：49/49 可评分、WAPE 36.25%） |
| 26 `POST /api/inventory/scenarios` 可取 `body.scenario.id` 或 `body.id` | ✔ 返回 `{scenario:{id,...}}`（`.get('id') or .get('scenario').get('id')` 均可取到） |
| 27 `PUT items/C001`（编号不在批次中）→ 200、upsert | ✔ 已按 Lead 补丁改为 upsert（name 缺省用 stock_code），200 |
| 28/29 入库 20→120、出库 30→90（取 `body.item.balance`） | ✔ 字段名与值一致 |
| 30 同 key 重复出库 `duplicate:true` 且余额 90 | ✔ 200 + duplicate:true + 同一流水 id |
| 31 出库 91 → 400 | ✔ 400 且说明余额 |
| 32 confirm 在途 10（`expected_date=2011-12-15`，过去日期） | ✔ 201；过去日期按有效在途计入（公式要求 `≤ 今天+提前期`） |
| 33 候选 = 80+30−90−10 = 10 | ✔ `candidate=10.0`，`lead_time_demand=80.0`，`effective_in_transit=10.0` |

## 7. 已知限制 / 未验证项（不粉饰）

1. **`replenishment/confirm` 的 `idempotency_key` 不做去重**：`inventory_in_transit` 表（Lead 的 schema）没有幂等键列与唯一索引，本模块按契约接收并校验该参数，但不承诺重复确认幂等；若验收需要，需 Lead 决定是否加列/加索引（schema 属 Lead 写作用域）。
2. 同参数同批次的任务缓存（契约 5.5 标注可选）未实现；每次提交都会新建任务。
3. 后台执行是**进程内线程**（红线禁止分布式队列）；服务重启时未完成任务由 `main.py` 标记 failed，已成功任务不受影响。
4. 仅在单进程 uvicorn 下验证；多 worker 部署下任务线程与状态库仍一致（SQLite 唯一索引/短事务），但未做多 worker 压测。
5. 未复跑 README 里 364 组合的历史回测基准；本次小窗口数字只作接口正确性证据，不用于「预测更准/更差」的结论。
6. 库存为人工模拟场景，`PUT` 参数无历史自动回填（初始值默认 0、提前期默认 7）；「提前期需求」是人工输入 `lead_time_days × daily_demand`，不是预测分摊。
7. 开发并行期其他模块（如 AI）文件可能临时不可导入，自测驱动的两次完整运行中曾遇到一次，等对方保存后重跑通过；与本模块代码无关。

## 8. 证据文件清单（工作区 `.tmp/`，临时目录，非交付物）

| 文件 | 内容 |
|---|---|
| `.tmp/fc_http_selftest.py` | 全链路自测驱动（启动/停服/重启，50 项断言） |
| `.tmp/fc_http_selftest.log` | 本次 50 项断言完整输出（`CHECKS=50 FAILURES=none`，`EXIT_CODE=0`） |
| `.tmp/rt_fc/server.log` | 隔离服务 stdout/stderr（含启动、请求日志、重启） |
| `.tmp/rt_fc/app.sqlite3` | 隔离状态库（jobs/inventory 持久化数据，供复查） |
| `.tmp/fc_inventory_service_probe.py` | 库存服务层固定样例（不依赖 HTTP/登录，25 项全 PASS） |
| `.tmp/fc_evidence_summary.py` | 从隔离状态库导出的任务/库存明细数字（本文件表格来源） |

原始批次库 `runtime/retail.sqlite3` 全程只读：运行前后大小与 mtime 均未变化（284,557,312 字节 / 2026-10-21 18:44:48）；`runtime/batches/` 未新增内容；未写入任何历史验收 JSON/日志。
