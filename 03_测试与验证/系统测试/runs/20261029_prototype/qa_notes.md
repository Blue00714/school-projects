# QA 记录：FastAPI 原型接口自动化测试（2026-10-29）

负责人：qa。范围：`03_测试与验证/系统测试/tests_api/**`、`runs/20261029_prototype/test_api.log`、
`acceptance_prototype.json`、`01_交付内容/原型说明.md`、`01_交付内容/README.md`。
本文件只记录**本轮真实运行**结果；历史 `acceptance.json`、`tests_result.log` 等旧证据未改动。

## 1. 环境与隔离

| 项 | 值 |
|---|---|
| 解释器 | CPython 3.12.10（`C:\...\Python312\python.exe`） |
| 系统 | Windows 10 (10.0.19045) x64，i7-12700KF，31.84GB RAM |
| 依赖实测 | pandas 3.0.5、numpy 2.5.3、lightgbm 4.6.0、openpyxl 3.1.5、FastAPI 0.115.6、uvicorn 0.32.1、pydantic 2.10.4（`.pydeps` 内置） |
| 服务启动 | 子进程 `python run.py --port <OS 分配空闲端口>`，每测试类一个实例，结束 terminate/kill |
| 数据隔离 | 临时 `ECE_RUNTIME_DIR` + 复制 `runtime/retail.sqlite3` 为 `ECE_ORIGINAL_DB`；正式库只读 |
| 测试数据 | `.tmp/qa_api/<类名>-<token>/`（跑完删除 285MB 库副本，保留 server.log） |
| 依赖新增 | 无（unittest + urllib + http.cookiejar + subprocess + 标准库） |

## 2. 运行命令与证据

```powershell
cd C:\Users\Administrator\11111\数据\学校项目
python "03_测试与验证/系统测试/tests_api/run_api_tests.py" --out test_api.log
python "03_测试与验证/系统测试/tests_api/build_acceptance.py"
```

| 证据 | 结果 |
|---|---|
| [test_api.log](test_api.log) | **58 项：通过 58，失败 0，错误 0，跳过 0；78.45s**；运行开始 2026-10-29T15:12:29+08:00 |
| [acceptance_prototype.json](acceptance_prototype.json) | **17 项关键断言：17/17 通过；10.89s** |
| 正式库完整性 | 运行前后 `retail.sqlite3` / `app.sqlite3` SHA-256 一致 →「官方库是否被修改：否」 |

`test_api.log` 头部含实际命令、Python/pandas 版本、官方库前后哈希；尾部含耗时、失败/跳过清单。

验收 JSON 关键数字（均为本轮接口实测返回）：

- 历史窗口 2011-11-10~2011-12-09：quantity **851403**、revenue **1694068.71**、orders **2913**、lines **86861**。
- 订单 total=2913 与看板一致；样本订单 581475：19 行明细合计 352 件 / 280.24 GBP。
- 导入小样例 8 行（2011-12-01~12-06）任务 succeeded、快照默认不切换；激活后 46 件 / 135.5 / 6 订单；重复导入 duplicate；切回原始恢复 851403。
- 预测：status=succeeded、origin=2011-12-09、7 行、actual 全 null、`daily-v1-hw-fixed-lgbm400`、blend_rule、CSV 7 行。
- 回测 2011-11-20~2011-12-01：12 起点、84 条预测、WAPE 可计算。
- 库存：余额 90；重复出库仍 90；出库 91 → 400；提前期需求 80、有效在途 10、候选 10。
- AI：`llm_configured=false`、`key_present=false`、provider `local-rule-planner`、工具 7 个、回答无工具外数字、简报 2601 字符 Markdown 可下载。
- 契约：openapi 与实际注册路由一致（43 个端点）；400/401/403/404 全部符合。

## 3. 失败项与处理（保留真实过程，不删用例）

第一轮试跑（15:06，`test_api_probe_full.log`）：58 项中 17 失败 / 1 错误。逐项归类后修正，最终 0 失败：

**测试自身期望错误（已改测试，未改产品代码）**
1. `test_forecast` 结果行没有 `origin` 字段（origin 在 result 顶层；回测才加到行上）→ 改为读 `result['origin']`。
2. 预测 CSV 数值四舍五入到 5 位小数，断言 6 位过严 → 容差 1e-4。
3. `FutureLeakTests` 误要求 actual 全为 null；`forecast()` 在起点后存在历史时会附 actual（回测使用），泄漏判据应只看预测值 → 只比较 prediction。
4. 商品排名是 top20，断言合计等于全店总量错误 → 改为 ≤ 总量 + 倒序 + 单行 ≤ 总量。
5. 4 年上限用例日期算成 1439 天（未超 1460）→ 改为 2007-01-01 起。
6. 空范围用例 2010-01-01 仍在覆盖范围内（正确返回 0）→ 改用覆盖外 2008-01-01。
7. `/api/transactions` 沿用既有契约不含 `customer_id`（CSV 导出才含）→ 调整字段期望。
8. 库存 `lead_time_demand` 只在 replenishment 响应中（契约 GET items 无此字段）→ 改在 replenishment 断言。
9. `weekdays` 只对实际有销售记录的行分组：历史窗口 4 个星期日无记录 → 6 组。改为 1..7 组、取值 0..6 唯一、与 trend 观测星期一致、合计=总量（不伪造未观测星期几）。
10. 未登录访问未知 `/api` 路径被中间件先拦为 401 → 已登录下断言 404，未登录接受 401/404。

**产品代码问题（已修，重跑通过）**
11. `/api/auth/login` 缺失/空 `username`、`password` 返回 401 "账号或密码错误"，与契约第 2 节"参数校验错误必须 400"不符 → 已由 Lead 在 `api/routers/auth.py` 修为 400 `bad_request`，凭据错误仍 401；本轮重跑通过（`test_auth.test_03`、`test_contract.test_04` 各 1 项）。

**AI 数字核对误报（已放宽批准集合，仍保留"工具外数字即失败"）**
12. 回答中引用了订单列表的订单号/单笔数量金额、商品检索总量、商品排名 stock_code 等工具真实输出，原批准集合仅看板 → 扩展为 看板（含国家筛选）+ 订单首页 + 商品检索 + 当前批次 manifest，仍拒绝任何工具外数字。

## 4. 未验证 / 未完成项

1. **真实大模型 API**：未配置、未验收；AI 目前仅本地规则层。30 题是固定抽样，非完整人工审核题集；本地规则对"同时要销量+销售额+订单数"的多指标问题可能只命中单一工具（实测：加"全店汇总"等词会落到 orders_query，仅"销量是多少"落到 dashboard_summary）——属映射质量待改进，不构成数字造假。
2. **性能**：未做每类接口 ≥100 次采样、5 并发、P95 与首次/预热区分；只记录单次耗时。
3. **`.xls`**：环境缺 `xlrd`，导入会 failed；测试只用 `.csv`/`.xlsx`。已上报 Lead 决策（补依赖或明确限制）。
4. **页面验收**：本套件只覆盖接口层；浏览器实际操作截图由前端组提供，未由 QA 独立复核每个页面。
5. **openapi 契约外端点**：`GET/PUT /api/settings` 已注册但契约第 5 节未列（非密钥），`test_contract` 记录为提示项，需契约同步。
6. 历史旧证据（`acceptance.json`、`tests_result.log`、`test_services.py` 16 项）未重跑，本文件不引用其结论。

## 5. 进程/端口说明

每次运行都是 OS 随机端口（本轮 5xxxx~6xxxx），测试结束 `terminate()` 后日志显示无残留。
诊断时发现 `127.0.0.1:8785`（PID 21508，15:07:16 启动）为其它成员/手工启动的服务，
**不是 tests_api 子进程**（本套件不监听固定端口 8785），未擅自结束；如需清理请由 Lead 确认归属。
