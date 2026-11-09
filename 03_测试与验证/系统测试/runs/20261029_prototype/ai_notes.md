# M5 AI 助手 / 运营简报 验收记录（ai-assistant）

- 执行时间：2026-10-29 15:17:26+0800；总耗时 21.4 秒
- 执行人/负责人：ai-assistant（M5）
- 代码文件：`api/routers/ai.py`、`api/services/assistant.py`、`api/services/brief.py`
- 固定题集：`03_测试与验证/系统测试/ai_acceptance_30.md`（30 题，真实结果同文件）
- 真实证据：`.tmp/ai_accept/acceptance_ai.json`（逐题响应摘要与断言）、`.tmp/ai_accept/server_*.log`（隔离服务端日志）

## 1. 环境与复现命令

```powershell
$env:ECE_RUNTIME_DIR="C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_ai_accept"
$env:ECE_ORIGINAL_DB="C:\Users\Administrator\11111\数据\学校项目\电商运营系统\runtime\retail.sqlite3"
$env:ECE_APP_DB="C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_ai_accept\app.sqlite3"
$env:ECE_ADMIN_PASSWORD="AdminPass123!"; $env:ECE_OPERATOR_PASSWORD="Operator123!"
$env:PYTHONPATH="C:\Users\Administrator\11111\数据\学校项目\电商运营系统\.pydeps"
cd "C:\Users\Administrator\11111\数据\学校项目\电商运营系统"; python run.py --port 8784
```

验收脚本：`python .tmp/ai_accept/run_ai_accept.py`（自动起停隔离服务、真实登录、30 题、简报、失败路径与降级路径）。
- 第一阶段（未配置密钥）：isolated runtime rt_ai_accept, port 8784；第二阶段（配置假密钥 + 不可达地址）：configured+failure, port 63238（8785 被其它进程占用时自动选择空闲端口）；第三阶段（进程内模拟库存模块导入失败）。

## 2. 端点与契约核对（真实 HTTP）

| 检查 | HTTP | 结果 | 说明 |
|---|---|---|---|
| GET /api/ai/status（未配置） | 200 | 通过 | 契约5.7：未配置时 fallback=local-rule-planner，不含密钥 |
| GET /api/ai/tools（7个只读工具） | 200 | 通过 | 工具白名单与参数 schema |
| 未登录访问 /api/ai/status | 401 | 通过 | 认证中间件生效 |
| operator 账号可读 AI 状态与问答 | 200 | 通过 | 普通登录用户均可读（契约5.7） |
| 空问题 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| scope 日期格式非法 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| scope start>end 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| scope 超过4年 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| scope 起始日超出覆盖 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| scope 未知国家 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| 未知字段 400 | 400 | 通过 | 参数校验错误必须是 400 且带 error/code |
| 无模拟库存场景时的库存降级 | 200 | 通过 | 库存模块未就绪/无场景时不 500，如实说明 |
| 准备模拟库存固定样例（HTTP） | 201 | 通过 | 通过 /api/inventory 接口写入，非直接改库 |
| 库存接口固定样例核对 | 200 | 通过 | 余额90、候选10（契约5.6固定样例） |
| POST /api/ai/ask 与 GET /api/dashboard 数字一致 | 200 | 通过 | 851403 / 1694068.71 / 2913 |
| POST /api/ai/brief（确定性模板） | 201 | 通过 | 含历史实际/预测/模拟标识、批次与口径、核心数字 |
| GET 简报下载（Markdown 附件） | 200 | 通过 | url 可直接下载且内容与响应 markdown 一致 |
| 不存在的简报 404 | 404 | 通过 | 错误契约 |

## 3. 30 题统计

- 总题数：30；通过：30
- 工具选择正确率：30/30
- 关键参数正确率：30/30
- 数字一致率：17/17（有数字断言的题）
- 追问（need_clarification）数：10
- 工具失败（failed）题数：1（Q30，预期行为）
- API/HTTP 失败数：0（0 表示 30 题均 200 且无 5xx）

| # | 题目 | 实际 status | 实际工具 | 数字断言 | 是否通过 |
|---|---|---|---|---|---|
| 1 | 2011-11-10到2011-12-09全店卖了多少 | ok | dashboard_summary | 一致 | 通过 |
| 2 | 2011年12月9日全店销售额是多少 | ok | dashboard_summary | 一致 | 通过 |
| 3 | 最近30天全店销量 | ok | dashboard_summary | 一致 | 通过 |
| 4 | 2011-01-01到2011-03-31英国卖了多少 | ok | dashboard_summary | 一致 | 通过 |
| 5 | 2011-11-10到2011-12-09商品85123A卖了多少 | ok | product_search / dashboard_summary | 一致 | 通过 |
| 6 | 2020-01-01到2020-01-31全店销量 | ok | dashboard_summary | — | 通过 |
| 7 | 最近哪些商品销量最高 | ok | product_ranking | 一致 | 通过 |
| 8 | 2011-12-09销量前5的商品 | ok | product_ranking | 一致 | 通过 |
| 9 | 2011-11-10到2011-12-09德国销量最高的商品 | ok | product_ranking | 一致 | 通过 |
| 10 | 搜索 PAPER CRAFT | ok | product_search | 一致 | 通过 |
| 11 | JUMBO BAG 卖了多少 | need_clarification | product_search | — | 通过 |
| 12 | 商品85123A的库存 | ok | product_search / inventory_query | 一致 | 通过 |
| 13 | WHITE HANGING HEART T-LIGHT HOLDER 库存 | ok | product_search / inventory_query | 一致 | 通过 |
| 14 | 某商品库存 | need_clarification | — | — | 通过 |
| 15 | 商品洗衣机的库存 | need_clarification | product_search | — | 通过 |
| 16 | 最近订单 | ok | orders_query | 一致 | 通过 |
| 17 | 订单536365 详情 | ok | orders_query | 一致 | 通过 |
| 18 | 2011-12-09 最近5笔交易 | ok | orders_query | 一致 | 通过 |
| 19 | 未来7天全店销量预测 | ok | forecast_7d | 一致 | 通过 |
| 20 | 以2011-11-30为起点预测未来7天 | ok | forecast_7d | 一致 | 通过 |
| 21 | 未来7天某商品销量预测 | need_clarification | — | — | 通过 |
| 22 | 未来3天销量预测 | need_clarification | — | — | 通过 |
| 23 | 当前数据批次和数据范围 | ok | batch_info | 一致 | 通过 |
| 24 | 商品23843的库存 | ok | product_search / inventory_query | — | 通过 |
| 25 | 帮我写一首诗 | need_clarification | — | — | 通过 |
| 26 | 利润是多少 | need_clarification | — | — | 通过 |
| 27 | 帮我生成运营简报 | need_clarification | — | — | 通过 |
| 28 | 2011-12-09到2011-11-10全店销量 | need_clarification | — | — | 通过 |
| 29 | 比上个月增长了多少 | need_clarification | — | — | 通过 |
| 30 | 以2020-01-01为起点预测未来7天 | failed | forecast_7d | — | 通过 |

## 4. 工具层实现与数字来源

| 工具 | 只读 | 数据来源 | 关键点 |
|---|---|---|---|
| dashboard_summary | 是 | `services.analytics.Analytics.dashboard` | 与 `/api/dashboard` 同一实现与口径，记录日期/商品/国家过滤 |
| product_search | 是 | `Analytics.search_products` | 商品编号或名称关键字，最多 50 条 |
| product_ranking | 是 | `Analytics.dashboard().ranking` | 按销量降序，支持国家/日期/`top_n` |
| orders_query | 是 | `services.data.Store` 固定只读 SQL | 订单按 invoice 去重，支持 order_key 明细与分页 |
| forecast_7d | 是 | `services.forecasting.forecast` | 全店未来 7 天，复用现有模型，标注“预测（非历史实际）” |
| inventory_query | 是 | `api.services.inventory`（队友模块）只读函数；不可用时回退状态库只读表 | 始终 `simulated: true`，公式与契约 5.6 一致 |
| batch_info | 是 | `services.batches.list_batches` + 批次 manifest | 批次、覆盖范围、来源文件、口径版本 |

- 所有数字均由上述工具真实计算；本地规则层只做“问题→工具参数”的确定性映射与结果排版，不自行计算或推断数值。
- 工具参数在入口统一校验：日期必须 `YYYY-MM-DD`、`start<=end`、范围 ≤4 年、商品编号/国家长度、`top_n 1..50`、`page_size 1..100`、`scenario_id` 字符集；非法 scope 返回 400（`bad_request`）。
- 工具白名单固定 7 个，无任意 SQL/Shell/文件/URL 工具；不执行任何写操作。

## 5. 安全与密钥保护

- 密钥只从环境变量 `ECE_AI_API_KEY` 读取（`settings.ai` 覆盖项只允许 base_url/model/timeout_seconds，接口明确拒绝密钥）。
- 所有出站字符串经 `_redact` 处理；`GET /api/ai/status` 只返回 `key_present: bool`，不返回 base_url/key；`ai_runs` 记录中不含密钥。
- 验收脚本对每个 ask/status/brief/下载响应做了 `sk-` 与哨兵密钥扫描，结果均为 0 命中（含配置假密钥的第二阶段）。

## 6. 本地规则层能力（30 题覆盖）

- 日期：显式区间（`2011-11-10到2011-12-09`）、年月日写法、相对“最近N天”、单日、倒序日期追问、超 4 年追问。
- 商品：编号精确匹配、英文名称/引号名称、同名取销量最高并在检索结果中列出两个编号、重名歧义追问、名称不存在追问。
- 国家：中文→英文映射（英国=United Kingdom）与英文全名匹配；国家名不会被误识别为商品名。
- 意图：看板汇总、商品检索、商品排名、订单/交易查询、全店未来 7 天预测、模拟库存、批次信息；利润/成本、商品级预测、非 7 天预测、简报生成、对比区间、非数据问题均给出具体追问。
- 缺日期（例如“帮我看看销量”）时按看板默认范围（数据覆盖末日往前 30 天）回答，并在答案里写明实际范围；该行为与契约 5.3/5.7 一致（Lead 已确认不强制追问）。
- 统一响应：`status/provider/llm_used/answer/clarification/tool_calls/range/batch/warnings/error/elapsed_seconds`，历史实际/预测/模拟/未知四类数据在答案中分别标注。

## 7. 大模型配置路径（第二阶段真实执行）

| 检查 | HTTP | 结果 | 说明 |
|---|---|---|---|
| 配置密钥后 /api/ai/status | 200 | 通过 | 密钥只来自环境变量；响应不回显 |
| settings.ai 覆盖 base_url/model/timeout | 200 | 通过 | PUT /api/settings 后生效；密钥仍只读环境变量 |
| 大模型不可达 → failed 保留工具结果 | 200 | 通过 | 不无限重试；工具数字保留；响应不含密钥 |

- 未配置密钥时：`provider=local-rule-planner`、`llm_used=false`、`fallback=local-rule-planner`、`model=""`。
- 配置密钥（base_url+key+model）后：`provider=openai-compatible`、`fallback=null`；`PUT /api/settings` 的 base_url/model/timeout 覆盖生效（与 lead 的 settings 口径一致）。
- 大模型不可达时：`status=failed`、保留已成功的工具结果与错误原因、单次调用不重试、响应不含密钥；答案保留本地规则结果并注明调用失败。

## 8. 库存模块并行开发容错（第三阶段真实执行）

| 检查 | HTTP | 结果 | 说明 |
|---|---|---|---|
| 库存模块导入失败 → 工具降级（不 500） | — | 通过 | 模拟 api.services.inventory 不可导入，回退只读表仍按契约公式给出90 |
| 库存模块导入失败 → /ask 降级回答 | — | 通过 | 降级路径仍返回模拟库存与提示，不伪造真实库存 |

- 正常情况下 `inventory_query` 调用队友的 `api.services.inventory` 只读函数（`list_scenarios` / `items_payload`）；模拟其导入失败后，工具自动回退到状态库只读表并按契约公式重算（余额 90、候选 10），并在 `warnings` 写明“库存模块未就绪”，不返回 500。

## 9. 简报（确定性模板）

- `POST /api/ai/brief` 生成 201：`{brief:{id,title,created_at,url}, markdown, provider:"template"}`；Markdown 含数据范围、批次、核心指标与环比、商品前 5、全店未来 7 天预测（预测局限）、模拟库存概览（`simulated=true` 标识）、数据来源与口径、生成方式（未调用大模型）。
- 简报写入 `briefs` 表；`GET /api/ai/briefs/{id}.md` 返回 `text/markdown; charset=utf-8` + `Content-Disposition: attachment`，内容与生成时 markdown 一致；不存在返回 404。
- 本次实测简报 2641 字符（见第 2 节检查表），30 题与简报均不含密钥明文。
- 持久化实测：`ai_runs` 写入 33 条（{'failed': 1, 'need_clarification': 10, 'ok': 22}），`briefs` 写入 1 条；简报下载由 `briefs` 表读取，不是内存临时返回。
- `POST /api/ai/brief` 的 `scenario_id` 会用于选择模拟库存场景（缺省取最近创建的场景），不存在时在简报中如实标注“指定场景不可用”。

## 10. 已知限制与遗留（不粉饰）

1. **真实大模型成功路径未验收**：当前环境无真实密钥，只验证了未配置与“配置后调用失败”两条路径；上线前需用真实 `ECE_AI_BASE_URL/ECE_AI_API_KEY/ECE_AI_MODEL` 复验一次成功回答，并按契约标注。
2. **AI 只处理单轮问题**：没有多轮上下文/会话记忆（契约未要求）；问题超过 1000 字符返回 400。
3. **预测仅支持全店未来 7 天**：商品/国家级预测、非 7 天预测都会追问，不伪造。
4. **模拟库存不是真实库存**：工具只读，任何写操作（入库/出库/补货确认）必须走“模拟库存”页面。
5. **商品编号存在大小写重复**（如 85123A/85123a）：名称解析取累计销量最高者，并在检索结果中列出，不合并两个编号的销量（避免改变既有 Analytics 口径）。
6. **数据没有成本字段**：利润/毛利类问题一律追问说明，不允许用销售额代替利润。
7. 本验收为课程原型范围；原始 Excel、`runtime/retail.sqlite3`、历史日志均未修改。

## 11. 关联文件

- `03_测试与验证/系统测试/ai_acceptance_30.md`：30 题固定集 + 真实结果。
- `电商运营系统/api/routers/ai.py`、`api/services/assistant.py`、`api/services/brief.py`：实现。
- `.tmp/ai_accept/run_ai_accept.py`：本次验收脚本（临时目录，不作为交付物）。
- 团队契约：`电商运营系统/API契约.md` 第 5.7 节。
