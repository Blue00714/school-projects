# 原型 API 契约 v0.2.0-prototype（施工图）

本文件是本次“雏形”阶段的唯一接口约定。所有模块按此实现；如需变更，先改本文件并在提交说明中写明原因，不允许各写各的。

- 项目根：`C:\Users\Administrator\Desktop\学校项目`（2026-11-02 起为唯一工作目录；原 `C:\Users\Administrator\11111\数据\学校项目` 镜像已停用，不再作为项目根）
- 系统目录：`电商运营系统/`
- 正式入口：`python backend/run.py`（FastAPI + uvicorn）。旧 `server.py` 保留作历史对照，不再是默认入口。
- 本阶段定位：可运行的课程原型（雏形），不是“完整交付”。真实 AI API 未接通，AI 状态只能标“待真实API验收”。

## 1. 文件归属（写作用域，冲突就停手并上报 Lead）

| 负责人 | 文件/目录 |
|---|---|
| Lead | `api/__init__.py`、`api/config.py`、`api/errors.py`、`api/security.py`、`api/db.py`、`api/deps.py`、`api/services/batches.py`、`backend/app/main.py`、`run.py`、`requirements.txt`、`.env.example`、`启动系统.*`、本契约 |
| api-auth | `backend/app/api/routers/auth.py`、`backend/app/api/routers/system.py`、`api/services/users.py`（可新建） |
| data-api | `backend/app/api/routers/analytics.py`、`backend/app/api/routers/imports.py`、`api/services/importer.py`、`api/services/orders.py`；`services/data.py` 仅允许“增量兼容”修改（必须保证既有 16 项测试通过） |
| forecast-inventory | `backend/app/api/routers/forecast.py`、`backend/app/api/routers/inventory.py`、`api/services/jobs.py`、`api/services/inventory.py` |
| ai-assistant | `backend/app/api/routers/ai.py`、`api/services/assistant.py`、`api/services/brief.py` |
| frontend | `web/index.html`、`web/app.js`、`web/style.css` |
| qa | `03_测试与验证/系统测试/tests_api/**`、`03_测试与验证/系统测试/runs/20261029_prototype/**`、`01_交付内容/README.md` 及新增说明文档 |
| 所有人 | 不得改 `20261014_电商数据与页面选型/online_retail_II.xlsx`、`runtime/retail.sqlite3`、`runtime/models/**`、历史验收 JSON/日志 |

共用底层已完成，直接 import 使用，不要重写：

```python
from api import config, db, deps
from api.errors import BadRequest, NotFound, Conflict, Forbidden, Unauthorized
from api.deps import current_user, require_admin, active_batch, get_batch, store_for, analytics_for, batch_public
from api.services.batches import get_batch, active_batch, list_batches, set_active, register_batch, read_manifest
from services.analytics import Analytics        # 既有统计口径，不要再改
from services.data import Store, prepare        # Store(dbsqlite路径)
from services.forecasting import forecast, backtest, METHODS, VERSION
```

认证中间件（`backend/app/main.py`）已负责：解析 Cookie → `request.state.user`；`/api/` 下除豁免路径外未登录一律 401；非 GET 请求做同源校验；统一错误 JSON。路由内部用 `Depends(current_user)` / `Depends(require_admin)` 再做角色校验。

## 2. 通用约定

- 统一前缀 `/api`，JSON UTF-8；响应字段用小写下划线。
- 错误体固定 `{"error": "人可读中文", "code": "bad_request|unauthorized|forbidden|not_found|conflict|unprocessable|upstream_failed|internal_error"}`。
- 状态码：400 参数/业务校验失败，401 未登录/会话失效，403 无权限/跨源，404 不存在，409 冲突（重复键），413 不做（超限用 400），500 内部错误。参数校验错误必须是 400（不是 FastAPI 默认 422）。
- 金额单位 GBP；`revenue` 两位小数（金额内部以 1/10000 整数存储，输出时 /10000）；数量为非负整数件；订单数按 `order_key=invoice` 去重。
- 日期 `YYYY-MM-DD`；时间 ISO 秒级；本机时区 Asia/Shanghai，数据库时间统一 UTC。
- 不要静默吞异常：业务失败必须让前端看到 `status/error`，不能把失败包装成成功。
- 响应中除既有契约外，凡涉及数据的都带当前批次：`batch: {id,label,kind}`；模拟库存响应带 `"simulated": true`。

## 3. 状态库 `runtime/app.sqlite3`（已建好，勿改结构；需要新表先找 Lead）

表：`users`、`sessions`、`batches`、`import_jobs`、`jobs`、`inventory_scenarios`、`inventory_items`、`inventory_in_transit`、`inventory_movements`、`ai_runs`、`briefs`、`settings`、`audit_log`。
字段以 `api/db.py` 的 `SCHEMA` 为准。连接统一 `with db.connect() as cx:`（自动 commit/rollback）。

关键约束：
- `batches` 只能有一个 `is_active=1`（部分唯一索引）。
- `inventory_movements(scenario_id, idempotency_key)` 唯一（非空时），重复提交必须幂等。
- `jobs.result_json` 保存完整结果（JSON 字符串），重启后仍可查询；启动时 `queued/running` 会被标记失败。
- 密码用 `api.security.hash_password/verify_password`；会话 token 只存 SHA-256（`security.token_digest`）。

## 4. 批次（数据快照）语义

- 每个批次是一个独立 sqlite：`original` → `runtime/retail.sqlite3`；上传批次 → `runtime/batches/<batch_id>/data.sqlite3`。
- 上传批次成功后 **不自动切换**；用户显式 `activate` 才切换。导入处理期间当前批次照常可查。
- 预测任务创建时绑定 `batch_id`，运行/结果不随切换变化。
- `manifest` 字段与原始数据一致：`source_file/source_sha256/policy_version/raw_rows/retained_rows/positive_sale_rows/cross_sheet_copies_excluded/within_sheet_duplicate_candidates_retained/cancelled_rows/nonpositive_quantity_rows/invalid_dates/quantity_invalid_rows/sale_rows_without_valid_price/zero_price_sale_rows/date_min/date_max/elapsed_seconds/rules`。
- 同一文件（sha256 相同）重复导入返回 `status: "duplicate"` 与既有 `batch_id`，不新建数据。

## 5. 端点清单

### 5.1 auth（api-auth）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | body `{username,password}`；成功 200 `{user}` + `Set-Cookie: ece_session=...; HttpOnly; Path=/; SameSite=Lax; Max-Age=<ttl>`；失败 401（账号不存在/密码错/已停用统一提示“账号或密码错误”） |
| POST | `/api/auth/logout` | 200 `{ok:true}`，撤销当前会话并清 Cookie |
| GET | `/api/auth/me` | 200 `{user}`；未登录 401 |
| POST | `/api/auth/password` | body `{old_password,new_password}`（新密码≥8位）；200 `{ok:true}`；撤销该用户其它会话 |

`user` 对象：`{id,username,display_name,role,active,must_change_password}`。

### 5.2 system（api-auth）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 免登录；`{ok:true,service:"ecommerce-ops-fastapi",version,data_ready,batch,ai_configured}` |
| GET | `/api/options` | 既有契约：`{manifest,countries,products:[{stock_code,name,quantity}],methods:{...}}`，追加 `batch` 与 `forecast:{scope:"whole-shop",horizon:7}`；结果按批次缓存（内存，切换批次失效） |
| GET | `/api/users` | admin；`{users:[user...]}` |
| POST | `/api/users` | admin；body `{username,password,display_name?,role:"admin|operator"}`；201 `{user}`；重名 409；密码<8位 400 |
| PATCH | `/api/users/{id}` | admin；body 任选 `{active?,role?,password?,display_name?}`；200 `{user}`；不能停用/降级自己；不能移除最后一个启用状态管理员（400） |

### 5.3 analytics（data-api）

筛选参数：`start,end,product,country`；默认 `end=manifest.date_max`、`start=end-29天`；规则沿用 `services/analytics.py`（4年上限、start<=end）。

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/products?q=` | 既有契约：数组 `[{stock_code,name,quantity}]`（≤50） |
| GET | `/api/orders` | 追加 `q,page,page_size`；`{filters,page,page_size,total,rows:[{order_key,date,first_time,last_time,customer_id,country,lines,quantity,revenue,product_codes:[...≤5]}],batch}`；排序 date desc, order_key |
| GET | `/api/orders/{order_key}` | `{order:{order_key,date,first_time,last_time,customer_id,country,lines,quantity,revenue},rows:[{invoice,stock_code,description,quantity,unit_price,revenue,invoice_time,customer_id,country,source_row,sheet}],batch}`；不存在 404 |
| GET | `/api/dashboard` | 既有契约字段全部保留，追加 `batch`；`filters/coverage/totals/previous/changes/trend/ranking/countries/weekdays/quality/warnings/policy_version` |
| GET | `/api/transactions` | 既有契约不变，追加 `batch` |
| GET | `/api/export/trend` | 既有契约 CSV（`date,quantity,revenue,orders,observed`），文件名 `sales-trend.csv` |
| GET | `/api/export/orders` | CSV `order_key,date,first_time,customer_id,country,lines,quantity,revenue` |
| GET | `/api/export/transactions` | CSV `invoice,stock_code,description,quantity,unit_price,revenue,invoice_time,customer_id,country,sheet,source_row` |

CSV 统一：UTF-8 BOM、`text/csv; charset=utf-8`、`Content-Disposition: attachment; filename=...`、公式注入中和（`=+-@` 开头加 `'`），长编号按字符串保留。

### 5.4 imports（data-api）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/imports` | multipart，字段 `file`（.xlsx/.xls/.csv，≤20MB），可选 `label`；成功 **202** `{job_id,status:"queued"}`；缺文件/类型不符/超限 400 |
| GET | `/api/imports/jobs/{job_id}` | `{job:{id,file_name,status,created_at,finished_at,error},report:{...manifest统计...},issues:[{level,row,field,message}],batch:{id,label,is_active}}`；status ∈ queued/running/succeeded/failed/duplicate |
| GET | `/api/imports/batches` | `{batches:[batch_public...],active_batch_id}` |
| POST | `/api/imports/batches/{id}/activate` | 200 `{batch,previous_batch_id}`；admin/operator 均可 |
| GET | `/api/imports/template.csv` | 示例表头 + 2 行样例（可下载） |

导入实现要求：后台线程执行，提交立即返回；解析/校验失败要写清缺列、非法日期/数量/单价、取消行、非正数量、重复候选；成功生成批次库并登记；**不覆盖**当前批次；同一 sha256 直接 duplicate。CSV 与 Excel 走同一清洗策略（可先转 DataFrame 再复用 `services.data.prepare` 的规则）。

### 5.5 forecast（forecast-inventory）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/forecast` | body `{origin?}`；202 `{job_id,status:"queued",batch_id}`；origin 缺省=批次 date_max；系统仅部署`robust_blend`，不接受model选择字段 |
| POST | `/api/backtest` | body `{start,end}`；1—56 天窗口；202 `{job_id,...}`；与预测共用唯一部署模型 |
| GET | `/api/forecast/latest` | 当前批次、当前模型版本最近成功的预测/回测，返回`{batch_id,jobs:{forecast?:job,backtest?:job}}`；页面刷新后恢复结果 |
| GET | `/api/jobs/{job_id}` | `{job:{id,kind,status,created_at,started_at,finished_at,elapsed_seconds,error,params,batch,result?}}`；result 结构沿用 `backend/app/services/forecasting.py` 返回值，追加 `batch_id`；重启后 succeeded 仍可查 |
| GET | `/api/jobs/{job_id}/export` | CSV `origin,date,h,prediction,baseline,actual,abs_error,model_version,data_policy_version,batch_id,actual_method`；未成功 400 |

任务排队/运行/成功/失败状态持久化到 `jobs` 表；同参数同批次重复提交可返回缓存任务（可选）；运行中断由启动逻辑标记 failed。

评分字段含 `MAE`、`RMSE`、`WAPE`、`BIAS`、`BIAS_PCT`、`MEDIAN_AE` 和 `P90_AE`。回测的 `complete_window_metrics` 逐日评分，`weekly_total_metrics` 先对每个完整窗口合计七天再评分，各有对应的基准结果。缺少实际值的目标日不评分。历史存档可缺少新字段。

### 5.6 inventory（forecast-inventory）

`simulated: true` 必须出现在所有库存响应中。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/inventory/scenarios` | `{scenarios:[{id,name,note,created_at,item_count}],simulated:true}` |
| POST | `/api/inventory/demo` | `{scenario,duplicate,simulated:true}`；当前批次12个活跃商品的持久化模拟场景；日需求参考近28天销量，初始库存/流水/在途为模拟；重复载入返回已有场景，保留用户修改 |
| POST | `/api/inventory/scenarios` | body `{name,note?,stock_codes?:[]}`；201 `{scenario}`；可一次性从当前批次建商品清单（默认初始0） |
| GET | `/api/inventory/scenarios/{sid}/items` | `{scenario,items:[{stock_code,name,initial_qty,lead_time_days,safety_stock,daily_demand,balance,in_transit_effective,in_transit_total,replenishment_candidate}]}` |
| PUT | `/api/inventory/scenarios/{sid}/items/{stock_code}` | body 任选 `{name?,initial_qty?,lead_time_days?,safety_stock?,daily_demand?}`；200 `{item}`；数值校验（≥0、提前期 0—365 整数） |
| POST | `/api/inventory/scenarios/{sid}/movements` | body `{stock_code,kind:"in"|"out",qty>0,idempotency_key?,note?}`；201 `{movement,item}`；出库余额不足 400；同 key 重复提交 200 `{duplicate:true,movement,item}` 不重复记账 |
| GET | `/api/inventory/scenarios/{sid}/movements` | `{movements:[...],simulated:true}`，按时间倒序，`limit` 默认200最大500 |
| GET | `/api/inventory/scenarios/{sid}/replenishment` | `{formula:"max(0, 提前期内场景需求 + 安全库存 - 当前库存 - 有效在途量)",items:[{stock_code,name,current_qty,lead_time_demand,effective_in_transit,safety_stock,candidate}],simulated:true}` |
| POST | `/api/inventory/scenarios/{sid}/replenishment/confirm` | body `{stock_code,qty,expected_date,idempotency_key?}`；201 `{in_transit,item}`；只登记在途，不直接加库存 |
| GET | `/api/inventory/scenarios/{sid}/in-transit` | `{items:[{id,stock_code,qty,expected_date,status,created_at}]}` |
| POST | `/api/inventory/scenarios/{sid}/in-transit/{iid}/receive` | 200 `{movement,in_transit,item}`；入库 + 扣减在途（同事务） |

计算定义：
- `balance = 初始库存 + Σ入库 − Σ出库`（单事务内更新流水与余额）。
- `lead_time_demand = lead_time_days × daily_demand`（人工模拟输入，不是预测分摊）。
- `effective_in_transit = Σ status='in_transit' 且 expected_date ≤ 今天+lead_time_days 的未入库数量`。
- `candidate = max(0, lead_time_demand + safety_stock − balance − effective_in_transit)`。
- 固定验收样例：初始100、入库20、出库30 → 余额90；同一出库重复提交仍为90；出库91被拒；提前期需求80、安全库存30、当前库存90、有效在途10 → 候选10。

### 5.7 ai（ai-assistant）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/status` | `{llm_configured,provider,model,key_present,timeout_seconds,fallback:"local-rule-planner"|null,tools_count}` |
| GET | `/api/ai/tools` | `{tools:[{name,description,params:{...},read_only:true}],policy:"仅只读；库存写操作必须走页面确认"}` |
| POST | `/api/ai/ask` | body `{question,scope?:{start,end,product,country,scenario_id?}}`；200 见下 |
| POST | `/api/ai/ask/stream` | 与 `/api/ai/ask` 使用相同的请求字段与会话认证。浏览器仅提交 `question`，返回 `application/x-ndjson` |
| POST | `/api/ai/brief` | body `{scope?,scenario_id?}`；201 `{brief:{id,title,created_at,url},markdown,provider:"template"}` |
| GET | `/api/ai/briefs/{id}.md` | `text/markdown; charset=utf-8`，`Content-Disposition: attachment`；不存在 404 |

`/api/ai/ask` 响应包含 `status`、`provider`、`llm_used`、`answer`、`clarification`、`tool_calls`、`range`、`batch`、`warnings`、`error`、`elapsed_seconds` 和 `stage_timings`。阶段计时单位为秒，含 `query_seconds`，调用模型时含 `model_seconds`，流中收到正文时含 `first_text_seconds`。

流接口每行一个 JSON 对象。按时间先收到 `{type:"stage",stage:"query"}`。有远程模型时，取数后发送 `stage:"answer"` 及批次、范围、工具摘要和取数耗时，再通过 `{type:"delta",text}` 发送已脱敏的正文。最终发送 `{type:"done",result}`，其中 `result` 沿用普通问答结果结构。流已开始后如遇服务错误，返回 `{type:"error",message}`。请求校验失败仍使用普通 JSON 错误。

规则：
- 未配置密钥：`llm_used=false, provider="local-rule-planner"`，用本地规则把问题映射到已声明工具，答案中的数字全部来自工具结果，并显式注明“未调用大模型”；**不得**伪造自然语言智能结论。
- 问题无法映射：`status:"need_clarification"` + 一个具体追问；日期/商品缺失且无默认范围时先追问。
- 配置了密钥但调用失败：`status:"failed"`，保留已成功的工具结果与错误原因，不无限重试。
- 工具限定在声明清单内，禁止任意 SQL/Shell/文件访问；按当前用户权限执行（admin/operator 均可读）。
- 回答必须带范围与批次标签；区分历史实际/预测/模拟/未知。

### 5.8 前端（frontend）

- Vue源码位于frontend/src，构建产物frontend/dist由FastAPI提供，不回退到旧版原生看板。
- 登录页：调用 `/api/auth/login`，成功后显示主界面；顶部显示当前用户、角色、数据批次（label + 覆盖范围）、“退出”。
- 标签页：看板、商品与订单、数据导入、预测与回测、库存与补货、AI 助手、系统管理（仅 admin 可见账号/设置）。
- 每个数据页显示：批次标识、覆盖范围、口径提示（销售额≠利润、无记录日补0、模拟库存、预测局限）。
- 错误显示后端 `error` 字段；401 时回到登录页。
- 导入页：上传、进度轮询、质量摘要、批次列表、切换按钮（切换后刷新看板）。
- 模拟库存页：场景选择/新建、商品参数、入库/出库、流水、补货建议（显示公式与“模拟”）。
- AI 页：状态横幅（是否配置真实大模型）、提问框、工具调用轨迹、简报生成与下载。

## 6. 测试约定（qa）

- 位置 `03_测试与验证/系统测试/tests_api/`，unittest + 标准库 urllib（不引入 pytest/httpx 依赖），每个用例用独立临时 `ECE_RUNTIME_DIR`，并复制 `runtime/retail.sqlite3` 作为原始批次（避免污染正式库）。
- 启动方式：子进程 `python backend/run.py --port <空闲端口>`，环境变量注入 `ECE_RUNTIME_DIR/ECE_ORIGINAL_DB/ECE_APP_DB/ECE_ADMIN_PASSWORD/ECE_OPERATOR_PASSWORD`；测试结束必须结束进程。
- 必须覆盖：登录/退出/未登录401/越权403；导入小样例→重复导入duplicate→切换→看板随批次变化→切回；订单总数与看板一致、明细相加一致；预测任务7行、回测小窗口、防泄漏（复用既有测试）；库存固定样例与幂等；AI未配置状态、工具数字与看板一致、简报Markdown可下载；404/400契约；openapi.json 路由与实际一致。
- 证据放 `runs/20261029_prototype/`：`test_api.log`、`acceptance_*.json`、页面截图；报告写实际命令、耗时、环境。

## 7. 红线

1. 不改原始 Excel、不删历史数据/模型/记录、不覆盖历史日志；新运行一律新建批次目录。
2. 不把模拟库存称为真实库存，不把销售额称为利润，不把预测误差改善称为经营收益。
3. 不伪造 AI 结果；无密钥必须显示未配置。
4. 不新增支付/购物前台/客服/画像/营销/自动调价/多店/多租户/分布式队列/大模型微调。
5. 后端必须校验权限，不能只隐藏前端菜单。
6. 任何“已完成”结论都要有本轮实际运行证据；跑不通就如实写失败。

## 8. 本轮实现现状与已知偏差（2026-10-29，原型批次）

按契约实现并已实测的部分见 `README.md` 与各模块 notes；以下是**尚未完全满足契约或需要下轮补齐**的差异，写在这里避免下轮误以为已具备：

| 项目 | 契约要求 | 本轮实际 | 影响/处理 |
|---|---|---|---|
| `.xls` 导入 | 接受 .xlsx/.xls/.csv | 支持 .xlsx/.csv；真实 `.xls` 需要 `xlrd`，当前环境未安装，会明确失败并提示“另存为 .xlsx” | 未用真实 `.xls` 样例验证；如需支持，下一轮加入 `xlrd` 依赖并补样例测试 |
| 补货确认幂等 | `confirm` 接受 `idempotency_key?` | 参数接收但未做唯一约束/去重（`inventory_in_transit` 表无该唯一列），重复确认会产生多条在途 | 出入库流水已严格幂等；在途确认如需幂等，下一轮给该表加列与唯一索引 |
| 同参数预测任务缓存 | 可选 | 未做；重复提交会新建任务 | 不影响正确性，只影响重复提交时的耗时 |
| AI 真实大模型 | 配置密钥后真实调用并验收 | 未配置密钥，仅本地规则工具层通过；真实 API 调用只能标“待真实 API 验收” | 已如实标注，未伪造 AI 结果 |
| 性能 P95 | 每类接口 ≥100 次采样、区分首冷 | 只做了单次/少量实测与 12 万行导入记录 | 未完成完整 P95 验收；不得在报告写“性能已验收” |
| 导入语义 | 每批完整快照+显式切换 | 已实现；批次库绝对/相对路径按运行目录回退（隔离 runtime 时记绝对路径） | 默认 `runtime/` 内记相对路径，迁移项目后仍可用 |
| 认证边界 | 未登录/越权分别拒绝 | 已实现；未登录访问**不存在**的 `/api/*` 路径由会话中间件先返回 401（已登录时才是 404），用于不泄露路径存在性 | 与契约“404 路径不存在”在未登录场景有差异，属安全取舍 |

## 9. 运行目录

- 后端启动入口：backend/run.py；应用包：backend/app。
- 本机配置：config/local；便携依赖：vendor。
- Vue源码：frontend/src；构建产物：frontend/dist；未构建时明确提示，不回退旧版看板。
- 运行数据和模型：runtime；MySQL现有数据目录保留runtime/mysql-data。
- 常规测试使用独立运行目录和数据库副本，正式数据库不用于测试写入。
