# M0 基线复核记录（20261029 原型交接）

- 复核人：M0 基线复核员（baseline-auditor）
- 复核时间：2026-10-29 14:45—14:48（Asia/Shanghai, UTC+08:00）
- 项目根目录：`C:\Users\Administrator\Desktop\学校项目`（桌面项目；会话工作目录 `C:\Users\Administrator\11111\数据\学校项目` 为空镜像，本报告按 Lead 要求写在镜像路径，由 Lead 统一同步回桌面）
- 复核性质：只读核查 + 跑既有测试 + 启动既有看板探测。未修改 `电商运营系统/`、`01_交付内容/`、原始 Excel、运行数据库、既有测试脚本与历史测试记录。
- 依据：`20261014_电商数据与页面选型/系统实施与交付完整清单_转交dsh.md` 第三节 M0；历史对照值见该清单第 121 行与 `03_测试与验证/系统测试/acceptance.json`。

## 结论摘要

| 项目 | 结论 | 要点 |
|---|---|---|
| 1 环境记录 | 记录完成，**存在版本风险** | Python 3.12.10；pandas **3.0.5** 与 requirements 的 `pandas>=2.2,<3.0` 冲突（实际环境未按声明约束安装） |
| 2 原始 Excel SHA-256 | **通过** | `bcbe73b3…f2e980` 与清单一致；45,622,278 字节；mtime 2026-10-14 12:06:05 |
| 3 运行库核对 | **通过** | sqlite/manifest/models 齐全；transactions=1,067,371；sale=1 为 1,022,290；forecast_runs=9；数据库只读打开且复核前后 SHA-256 不变 |
| 4 既有 16 项服务测试 | **通过 16/16** | `Ran 16 tests in 4.457s / OK`，退出码 0；**pandas 3.0.5 下未出现失败** |
| 5 既有看板接口与历史对照值 | **通过** | totals 精确等于 851,403 件、1,694,068.71 英镑、2,913 订单、86,861 正向明细；探测后进程已关闭 |
| 6 真实数据回测验收 | **未验证（按任务范围未执行）** | `tests/verify_real.py` 含回测，需另起 8777 服务；本次未运行，旧验收结论不代表本次复现 |
| 7 其他遗留 | **未验证** | 官方原文件逐字节比对未做（数据来源为公开镜像）；M0 清单中的 Git 接手快照未建立（只读审计范围外） |

---

## 1. 环境记录

证据：`env.log`（同目录）。

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10（build 10.0.19045），x64 |
| CPU | 12th Gen Intel(R) Core(TM) i7-12700KF（Family 6 Model 151 Stepping 2），逻辑处理器 20 |
| 内存 | 31.84 GB（探测时可用 17.93 GB） |
| PowerShell | 5.1.19041.6456 |
| Python | 3.12.10（`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`，无虚拟环境） |
| pandas | **3.0.5** |
| numpy | 2.5.3 |
| openpyxl | 3.1.5 |
| lightgbm | 4.6.0 |
| scikit-learn | 1.9.0 |
| 其他 | joblib 1.6.0、scipy 1.18.1、python-dateutil 2.9.0.post0、tzdata 2026.3 |

`电商运营系统/requirements.txt` 声明：

```
pandas>=2.2,<3.0
numpy>=1.26,<3.0
openpyxl>=3.1,<4.0
lightgbm>=4.0,<5.0
scikit-learn>=1.4,<2.0
```

**风险点（如实记录）**：实际安装 pandas 3.0.5 **超出** requirements 的 `>=2.2,<3.0` 上界（numpy/openpyxl/lightgbm/scikit-learn 均在声明范围内）。当前 16 项既有测试在 3.0.5 下仍全部通过，因此这属于**依赖声明与实际环境不一致的版本漂移风险**，尚未观察到功能失败；但环境不可按 requirements.txt 原样复现，建议后续由负责人在“锁版本/放宽声明/建独立环境”之间做一次明确决定，本次不修改任何依赖文件。

## 2. 原始 Excel 核对

证据：`integrity.log` 第 [1] 节。

| 项 | 值 |
|---|---|
| 路径 | `C:\Users\Administrator\Desktop\学校项目\20261014_电商数据与页面选型\online_retail_II.xlsx` |
| 大小 | 45,622,278 字节（43.51 MiB） |
| 修改时间 | 2026-10-14 12:06:05 |
| 创建时间 | 2026-10-14 11:52:20 |
| 实测 SHA-256 | `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980` |
| 清单期望 | 同上 |
| 比对结果 | **一致（True）** |

补充：`数据说明.txt` 记载该文件来自公开镜像（raw.githubusercontent.com，KristerMartinez/online_retail_ii），**尚未与 UCI 官方原文件逐字节比对**（`data_manifest.json` 中 `source_kind=public_mirror`、`official_byte_comparison_completed=false`）。哈希一致只能证明与清单基线一致，不能证明与官方原文件一致。

## 3. 运行库核对

证据：`integrity.log`、`sqlite_probe.log`。

### 3.1 文件是否存在

| 文件 | 存在 | 大小 | 修改时间 |
|---|---|---|---|
| `电商运营系统/runtime/retail.sqlite3` | 是 | 284,557,312 字节 | 2026-10-21 18:44:48 |
| `电商运营系统/runtime/data_manifest.json` | 是 | 1,728 字节 | 2026-10-21 18:23:43 |
| `电商运营系统/runtime/models/` | 是 | 4 个模型目录，每个含 `lightgbm.txt` + `metadata.json`（lightgbm.txt 580,932 或 582,613 字节） | 2026-10-21 18:14—18:44 |

### 3.2 data_manifest.json 关键字段（原文见 integrity.log）

| 字段 | 值 |
|---|---|
| raw_rows | 1,067,371（Year 2009-2010: 525,461；Year 2010-2011: 541,910） |
| cross_sheet_copies_excluded | 22,523（全部来自 Year 2010-2011） |
| retained_rows | 1,044,848 |
| positive_sale_rows | 1,022,290 |
| cancelled_rows | 19,165 |
| within_sheet_duplicate_candidates_retained | 11,812 |
| nonpositive_quantity_rows | 22,557 |
| sale_rows_without_valid_price | 5 |
| zero_price_sale_rows | 2,631 |
| invoice_with_multiple_times | 83 |
| invalid_dates / quantity_invalid_rows | 0 / 0 |
| date_min / date_max | 2009-12-01 / 2011-12-09 |
| policy_version | `cross-sheet-multiset-v1-sales-v2-invoice-order` |
| order_key | `invoice` |
| order_identity_conflicts | 0 |
| prepared_at | 2026-10-21T18:03:58 |
| source_sha256 | 与原始 Excel 实测一致 |

### 3.3 SQLite 只读查询

打开方式：`sqlite3.connect("file:C:/Users/Administrator/Desktop/学校项目/电商运营系统/runtime/retail.sqlite3?mode=ro", uri=True)`，仅执行 SELECT/PRAGMA，未写回。

| 查询 | 结果 | 与 manifest/预期 |
|---|---|---|
| 表 | `transactions`、`forecast_runs`、`metadata`（+ 索引） | — |
| `select count(*) from transactions` | **1,067,371** | 等于 raw_rows |
| `select count(*) from transactions where sale=1` | **1,022,290** | 等于 positive_sale_rows |
| `select count(*) from forecast_runs` | **9** | 历史预测任务记录（3 组时间戳 × 3 个任务） |
| sale=1 的日期范围 | 2009-12-01 .. 2011-12-09（n=1,022,290） | 等于 manifest |
| `journal_mode` | `delete` | — |
| 数据库 SHA-256（复核前→后） | `033f4aa2972771fddd95045f6f2de0a63af1d9c744090778a76c8fb803ef61bf` → 同值 | **未被修改**；mtime 仍为 2026-10-21 18:44:48 |

`forecast_runs` 的 9 条记录均为历史任务（3 个任务 × 2026-10-21 的 18:14/18:25/18:44 三批）：`blend_rule` 方法，origin 为 2011-12-09、2011-11-01 的 7 日预测及 2011-10-15..2011-12-09 回测，payload 长度约 1.7KB—99KB。此为历史累积，不是本次新增。

## 4. 既有服务测试（16 项）

### 4.1 执行方式

- 工作目录：`C:\Users\Administrator\Desktop\学校项目\03_测试与验证\系统测试`
- 命令：`python -m unittest discover -s tests -p test_services.py -v`
- 环境：Python 3.12.10 / pandas 3.0.5；`tests/test_services.py` 通过 `parents[3]/电商运营系统` 引入源码，未改动测试脚本。
- 原始输出：`test_services.log`（完整 16 行测试结果 + unittest 汇总 + 运行元信息）。

### 4.2 结果

```
Ran 16 tests in 4.457s
OK
M0_RERUN started_at=2026-10-29 14:46:50 finished_at=2026-10-29 14:46:55 duration_s=5.3 exit_code=0 python=Python 3.12.10
```

- 通过 **16 / 16**；失败 0，报错 0，跳过 0；进程退出码 0。
- `ForecastTests` 8 项全部通过：未来值不影响预测、始终返回 7 天、重叠回测统计、WAPE/MAE 边界、非法历史与日期、回退方法具名、零销量星期候选特征、原生模型保存。
- `DataAndAnalyticsTests` 8 项全部通过：跨表副本口径与源文件不可变、totals 与明细/排名一致、筛选/空区间/分页、日序列补 0、同单分钟级时间差不拆单、订单身份冲突需复核、HTTP 查询/错误码/静态目录边界、CSV 公式注入中和。
- 日志中夹带的 `GET /api/...` 行是测试内临时 HTTP 服务（`ThreadingHTTPServer`，随机端口）的访问日志，属测试内容，不是失败信息；已扫描全部日志，无 `FAIL`/`ERROR`/`Traceback`。

### 4.3 pandas 3.0 影响判断

本次 **未出现任何 pandas 3.0 引发的失败或异常**。与历史记录（`tests_result.log`：2026-10-21，`Ran 16 tests in 4.607s / OK`）相比，用例数、通过数与耗时基本一致。

判断：**不存在“环境版本导致既有测试失败”的问题**；但测试只使用合成小样例（`test_tmp` 下的小 xlsx/小 sqlite），**不能**证明 pandas 3.0.5 下真实 1,047,448 行数据重建（`prepare`）无差异——真实数据重建本次未执行（见第 7 节未验证项）。因此“pandas 3.0.5 风险”应保留为待跟踪项，而不是已关闭项。

### 4.4 写入副作用（符合历史惯例）

测试按既有设计把临时数据写入包内 `test_tmp/`（`WorkspaceScratch` 使用 uuid 目录，保留不清理）。本次两次执行后 `test_tmp` 总数 48 个目录，其中 18 个为本次新建；`tests/__pycache__` 因导入测试而被 Python 重新生成（字节码缓存，非源码修改）。这些均在允许范围内。

## 5. 既有看板接口与历史对照值

### 5.1 启动与探测

- 工作目录：`C:\Users\Administrator\Desktop\学校项目\电商运营系统`
- 启动命令（后台）：`python -c "from server import serve; serve(8899)"`（8899 未占用；原生 `http.server` + services，非 FastAPI）
- 探测：`Invoke-WebRequest` 请求三个接口；原始摘要见 `serve_probe.log`，完整 dashboard JSON 见 `dashboard_probe.json`，options 见 `options_probe.json`，服务端日志见 `serve_stdout.log`（stderr 为空）。
- 说明：首次启动因 PowerShell `Start-Process -ArgumentList` 将 `-c` 后含空格的代码拆词而报 `SyntaxError`，属本次操作命令引用错误，非系统缺陷；改为整体加引号的参数后一次启动成功（日志已覆盖为成功那次的输出）。

| 请求 | HTTP | 摘要 | 耗时 |
|---|---|---|---|
| `/api/health` | 200 | `{"ok": true, "data_ready": true}` | 0.011 s |
| `/api/options` | 200 | countries=43，products=500，methods=blend_rule/lgbm/holt_winters/seasonal_week4/naive_week，coverage=2009-12-01..2011-12-09，policy=`cross-sheet-multiset-v1-sales-v2-invoice-order` | 1.145 s |
| `/api/dashboard?start=2011-11-10&end=2011-12-09` | 200 | 见下表 | 0.418 s |

### 5.2 历史对照值比对（关键）

窗口 2011-11-10 至 2011-12-09（含首尾日，全店，无商品/国家筛选，`policy=cross-sheet-multiset-v1-sales-v2-invoice-order`）：

| 指标 | 历史对照值 | 本次实测 | 结论 |
|---|---|---|---|
| totals.quantity | 851,403 | **851,403** | **一致** |
| totals.revenue（GBP） | 1,694,068.71 | **1,694,068.71** | **一致** |
| totals.orders | 2,913 | **2,913** | **一致** |
| totals.lines（正向明细） | 86,861 | **86,861** | **一致** |
| totals.products | — | 2,930 | 记录 |
| totals.amount_rows（有效金额行） | — | 86,861 | 记录 |
| totals.average_order_value | — | 581.5546549948507 | 记录 |
| trend 天数 / ranking / countries | — | 30 / 20 / 10 | 记录 |
| warnings | 无 | 空 | 一致 |
| previous.complete | — | True | 记录 |
| previous.totals（2011-10-11..2011-11-09） | — | quantity=655,756，revenue=1,229,356.5，orders=2,278 | 记录 |
| changes | — | quantity +29.84%，revenue +37.80%，orders +27.88% | 记录 |

**结论：看板历史对照值在本次环境中原样复现，属于“通过”。**

### 5.3 进程关闭

探测结束已 `Stop-Process -Id <pid> -Force`；`HasExited=True`，再次检查端口无 `LISTENING`，仅剩内核 `TIME_WAIT` 状态的已关闭连接（不监听、不影响后续启动）。服务端 stdout 末尾记录 4 条 200 访问日志。

## 6. 证据文件清单（均在本目录）

| 文件 | 内容 |
|---|---|
| `m0_baseline.md` | 本报告 |
| `env.log` | 环境、依赖版本、requirements 对照与风险 |
| `integrity.log` | Excel 哈希/大小/时间、DB 前后哈希、manifest 原文、models 清单、写入范围核对、只读计数 |
| `sqlite_probe.log` | 只读 sqlite 查询原始输出（表、行数、日期范围、forecast_runs、metadata、DB 哈希前后） |
| `test_services.log` | 16 项服务测试完整输出 + 运行元信息 |
| `serve_probe.log` | 看板三接口状态码/摘要/耗时与预期对照 |
| `serve_stdout.log` / `serve_stderr.log` | 看板服务端 stdout（启动 + 访问日志）/ stderr（空） |
| `dashboard_probe.json` | `/api/dashboard` 完整响应（30 天趋势、排名、国家、质量指标等） |
| `options_probe.json` | `/api/options` 完整响应（43 国家、500 商品、5 方法） |

## 7. 未验证 / 遗留事项（不粉饰）

1. **真实数据回测验收未执行**：`tests/verify_real.py` 需要另起 8777 端口并对真实库含首尾日窗口做回测，按任务约定本次不跑（`03_测试与验证/系统测试/README.md` 亦说明“不能为检查搬迁而顺手执行”）。`acceptance.json`/`acceptance.log` 为 2026-10-21 历史记录，本次未复现，其结论不能当作本次结果。
2. **pandas 3.0.5 未做真实数据重建验证**：既有 16 项测试使用合成样例；真实 1,067,371 行 Excel 的 `prepare` 重建（含跨表副本排除等）本次未重跑，pandas 3.0 在此路径上的行为差异仍属未验证。
3. **官方原文件逐字节比对未做**：数据来源为公开镜像；哈希一致仅等于清单基线。
4. **M0 清单其他项不在本次范围**：Git 接手快照/回退点、技术栈定稿、报告表述同步、M1—M6 排期、向负责人收集信息等未执行（本次仅做只读核查）。FastAPI 应用尚不存在，当前可启动的是原生 `http.server` 看板（与 M1 迁移前的现状一致）。
5. **环境可复现性风险**：requirements.txt 与已安装 pandas 3.0.5 不一致；无虚拟环境/锁文件。建议后续在 M1 前明确依赖策略，但本次不改动依赖与代码。

## 8. 只读合规说明

- 未修改 `电商运营系统/` 下任何源码、模型、数据库与 manifest；复核前后 `retail.sqlite3` SHA-256 相同、mtime 未变。
- 未修改原始 Excel（仅计算哈希/属性）与 `01_交付内容/`。
- 写入仅发生在 `03_测试与验证/系统测试/runs/20261029_prototype/`（本目录，证据与报告）与历史约定的 `03_测试与验证/系统测试/test_tmp/`（测试临时目录）、以及 Python 重新生成的 `tests/__pycache__` 字节码缓存。
