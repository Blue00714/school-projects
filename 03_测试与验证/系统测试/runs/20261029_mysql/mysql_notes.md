# MySQL 迁移记录（task-7：数据层方言化 + 真实 MySQL 8 接入 + 数据迁移）

日期：2026-10-29。执行人：mysql-backend。方案依据：`电商运营系统/技术栈迁移方案.md` 第 2—4 节。
证据文件（同目录）：`mysql_acceptance.log`（MySQL 模式逐项 PASS 记录）；SQLite 回归日志
`.tmp/mysql_regression.log`（由 `03_测试与验证/系统测试/runs/20261029_prototype/mysql_regression.log` 复制）。

## 1. 结论

| 项目 | 结果 |
|---|---|
| 项目内便携 MySQL 8.0.28 | 已部署在 `电商运营系统/.mysql/`，数据目录 `runtime/mysql-data/`，仅监听 `127.0.0.1:3307` |
| 迁移后库 | `ece_app`（应用状态）、`ece_original`（原始数据，1,067,371 行）；上传批次生成 `ece_batch_<id>` |
| 数据一致性 | 全部 PASS：transactions 1,067,371；sale=1 1,022,290；看板 2011-11-10~12-09 = 851,403 件 / £1,694,068.71 / 2,913 单 / 86,861 明细；manifest 逐字段一致 |
| MySQL 模式验收 | 全项 PASS：登录、看板、订单、商品、导入→duplicate→切换→切回、预测 7 行+CSV、库存 90/幂等/91 拒/候选 10、AI status/ask |
| SQLite 模式回归 | 服务测试 16/16 通过（两种隔离方式）；API 测试 **58/58 通过**（修复 SPA 兜底后复跑，官方库运行前后 sha256 未变） |
| 回退 | `ECE_DB_BACKEND` 未设且 `runtime/mysql.env` 不存在 → 自动 SQLite，原行为不变 |

## 2. 环境事实（本机实测）

```
OS            Windows（PowerShell，AUST 中文界面），系统 ANSI 代码页 936
Python        3.12.10（系统解释器）
MySQL         mysql-8.0.28-winx64.zip（阿里云镜像，222,021,254 bytes，来自 .tmp/mysql/）
服务器        8.0.28，port=3307，bind_address=127.0.0.1
字符集        utf8mb4 / utf8mb4_0900_ai_ci，max_allowed_packet=268435456(256M)
连接驱动      PyMySQL 1.2.3（纯 Python，已解包到 电商运营系统/.pydeps/pymysql）
认证插件      mysql_native_password（避免 cryptography 依赖）
数据库        ece_app / ece_original（+ MySQL 自带 4 个系统库）
账号          'ece'@'127.0.0.1'（另建 'ece'@'localhost' 便于本机客户端），ALL PRIVILEGES ON *.*（仅本地开发实例）
输出/日志     runtime/mysql-logs/error.log
```

账号口令：`scripts/mysql_init.ps1 → scripts/mysql_bootstrap.py` 生成 24 字符随机口令，写入
`电商运营系统/runtime/mysql.env`（`ECE_MYSQL_PASSWORD`），该文件被 `.gitignore` 的 `runtime/` 覆盖，**不入库**；
删除该文件即回到 SQLite 模式。一次性登录口令在 `runtime/initial_admin.txt`（同样不入库）。

## 3. 部署与启停（幂等）

```powershell
powershell -ExecutionPolicy Bypass -File 电商运营系统\scripts\mysql_init.ps1     # 首次：解压+my.ini+初始化+启动+建账号/库/写 mysql.env
powershell -ExecutionPolicy Bypass -File 电商运营系统\scripts\mysql_init.ps1 -Force  # 重建（会清空 data）
powershell -ExecutionPolicy Bypass -File 电商运营系统\scripts\mysql_start.ps1    # 启动（已监听则直接返回 0），等待端口就绪
powershell -ExecutionPolicy Bypass -File 电商运营系统\scripts\mysql_stop.ps1     # mysqladmin shutdown，失败回退 PID
```

脚本行为与实测输出：

```
[mysql-init] mysql home: ...\电商运营系统\.mysql
[mysql-init] my.ini written (ANSI cp936)
[mysql-init] data directory initialized (root@localhost has an EMPTY password; local only)
[mysql-start] starting mysqld (127.0.0.1:3307)
[mysql-start] ready on 127.0.0.1:3307
[bootstrap] connected as root (empty password, first run)
[bootstrap] grants applied; databases now: ece_app, ece_original
[bootstrap] credentials written to ...\runtime\mysql.env (never commit this file)
[bootstrap] verified as ece@127.0.0.1 on ece_app, server 8.0.28
```

`my.ini`（`电商运营系统/.mysql/my.ini`）关键项：`basedir/.mysql`、`datadir=runtime/mysql-data`、
`port=3307`、`bind-address=127.0.0.1`、`mysqlx=0`、`character-set-server=utf8mb4`、
`default_authentication_plugin=mysql_native_password`、`max_allowed_packet=256M`、`log-error=runtime/mysql-logs/error.log`。

**踩坑记录（换机必看）**：
1. MySQL 8 在 Windows 上按**系统 ANSI 代码页**读取 `my.ini`。本项目路径含中文，最初用 ASCII 写 my.ini
   导致 `datadir=C:/.../??/????/...` → `Can't create/write to file ... (OS errno 2)`。现脚本用
   `[System.Text.Encoding]::GetEncoding(<CurrentCulture ANSI 码页>)` 写 ANSI（本机 cp936）。
   实测 UTF-8 写 my.ini 会失败（初始化报 share/errmsg.sys 找不到 / datadir unusable）。
2. 沙箱/CI 会在“一条命令结束”时杀掉该命令的子进程树，因此 `mysqld` 必须放在长驻会话（后台作业/终端）里启动；
   若单独调用 `mysql_start.ps1` 后马上另起命令，端口可能已被回收。日常使用与本机开发无此问题。
3. 端口固定 3307；初始化后 root@localhost 为空口令（仅本机、仅开发用），应用只用 `ece` 账号。

## 4. 方言层实现（改动文件与兼容点）

改动文件：`api/db.py`、`services/data.py`、`services/analytics.py`、`api/services/batches.py`、
`api/services/importer.py`、`api/deps.py`（`api/deps.py` 复用 `db.resolve_db_path`，未改逻辑）、
`scripts/`（新增 4 个脚本）。`api/main.py`、`config.py`、`.env.example`、启动脚本、`requirements.txt` 未改。

`api/db.py` 统一连接抽象：
- `ECE_DB_BACKEND=sqlite|mysql`（默认 sqlite）→ `connect(path=None, database=None)` 返回 SQLite 原生连接或 MySQL 包装连接，均支持 `.execute/.executemany/.executescript/.commit/.rollback/.close` 与 `with` 语义；
- 业务 SQL 一律写 `?`；MySQL 侧扫描（尊重引号）后替换 `%s`，并把字面 `%` 转义为 `%%`；
- 行对象 `Row(dict)` 同时支持 `row['col']` 与 `row[0]`（`sqlite3.Row` 的两种用法都保留）；
- `executescript` 先剥离 `--`/`/* */` 注释再按顶层分号拆分（注释里的撇号曾吞掉语句终止符）；`CREATE INDEX`/`ALTER` 的“已存在”错误（1050/1061/1060/1091/1826）静默忽略；
- `PRAGMA` 只在 SQLite 分支执行；MySQL 分支自动 `ensure_database`（缓存）、会话隔离级别设为 **READ COMMITTED**（幂等键竞争后能读到赢家已提交行）；
- PyMySQL 异常映射为 `sqlite3.IntegrityError/OperationalError/DatabaseError`，因此既有 `except sqlite3.IntegrityError`（库存幂等、用户名唯一）等分支无需改动；
- MySQL 保留字兼容：`settings.key`/`metadata.key` 用反引号；SQLite 原型把 `LINES` 当列别名（`count(*) lines`），方言层改写为 `` count(*) `lines` ``（`api/services/orders.py`、`api/services/assistant.py` 不改）；
- `BEGIN IMMEDIATE`（SQLite 写锁）→ MySQL `START TRANSACTION`；
- 应用状态库 DDL：`SCHEMA_MYSQL`（AUTO_INCREMENT、VARCHAR/MEDIUMTEXT、`active_key` 生成列模拟 SQLite 部分唯一索引、`rowid` 自增列供 `inventory` 的 `order by rowid` 使用、CHECK/外键等价约束）。

`services/data.py`：`Store` 支持 SQLite 路径或 MySQL database（`mysql:<db>` 或裸库名），`manifest()`/`series()` SQL 不变；
`build_from_frames` 在 MySQL 下 `CREATE DATABASE` + 显式 DDL + **2000 行/批 executemany** 写入（`DROP TABLE IF EXISTS` 后重建，保证幂等）；
`prepare()` 两种后端都能重建缓存；`MANIFEST_SQL` 用 `key` 反引号。

`services/analytics.py`：**只改 weekday 表达式**（SQLite `cast(strftime('%w',date) as integer)` ↔ MySQL `(DAYOFWEEK(date)-1)`），其余 SQL 未动。

`api/services/batches.py`：`db_path` 在 MySQL 存 `mysql:<database>`（`db.stored_target`），`read_manifest` 支持从 MySQL `metadata` 读取；
`ensure_original_batch` 以 `mysql:ece_original` 登记 `original` 批次。

`api/services/importer.py`：MySQL 下批次库名 `ece_batch_<batch_id>`，失败时 `DROP DATABASE` 清理半成品。

## 5. 数据迁移（SQLite → MySQL，保真、快）

```powershell
cd 电商运营系统
python scripts\migrate_to_mysql.py
```

实际输出（节选，2026-10-29 16:01）：

```
[migrate] ...\runtime\retail.sqlite3 (271.4 MB) -> MySQL 127.0.0.1:3307/ece_original
[migrate] server 8.0.28
  rows 1,067,371 (25,320/s)
[migrate] transactions copied (18 columns) in 42.5s
[migrate] metadata copied (1 keys: manifest) in 0.5s
  indexes: 7 created, 0 already existed
[migrate] indexes ready in 69.2s
[migrate] verifying row counts / aggregates / manifest
  PASS transactions: 1067371
  PASS sale=1: 1022290
  PASS retained_rows==sqlite: 1044848
  PASS quantity==sqlite: 11490121
  PASS amount_units==sqlite: 205337419180
  PASS orders==sqlite: 41943
  PASS cancelled_rows==sqlite: 19165
  PASS duplicate_candidates==sqlite: 11812
  PASS date_min==sqlite: '2009-12-01'
  PASS date_max==sqlite: '2011-12-09'
  PASS window 2011-11-10~12-09 lines: 86861
  PASS window quantity: 851403
  PASS window amount_units: 16940687100
  PASS window orders: 2913
  PASS manifest fields identical: {...}
[migrate] initializing application state database
  app database: mysql:ece_app
  accounts: admin/admin, operator/operator
  batch original: mysql:ece_original active=1
[migrate] finished in 131.4s, all checks passed
```

说明：manifest 原样搬移（含 `source_sha256=bcbe73b3…`、`policy_version=cross-sheet-multiset-v1-sales-v2-invoice-order`、
`date_min=2009-12-01`、`date_max=2011-12-09`、`retained_rows=1044848` 等），不是重新解析 Excel，因此口径与 SQLite 原型逐字段一致；
脚本退出码非 0 表示某项核对失败。

迁移后实测（`SHOW`/`COUNT`）：

```
transactions = 1067371, sale=1 = 1022290
ece_original: transactions / metadata(1 行 manifest) / forecast_runs
ece_app     : users(2) batches(1 original → mysql:ece_original, active=1) 其余状态表
transactions 索引 8 条（date/sale/stock/country/order + DDL 内唯一键）
```

## 6. MySQL 模式验收（命令 + 结果）

验收脚本（真实 `python run.py` 子进程 + 真实 MySQL；应用库用独立 `ece_app_test`，运行前后自动建/删，不污染 `ece_app`）：

```powershell
cd 电商运营系统
python scripts\acceptance_mysql.py
```

实测（完整逐项记录见 `03_测试与验证/系统测试/runs/20261029_mysql/mysql_acceptance.log`）：

```
服务已启动 http://127.0.0.1:xxxxx（4s）
[1] health 200 / batch=original；登录 admin 200；/api/auth/me 200；MySQL 实际连接 ece@127.0.0.1 / ece_app_test
[2] dashboard 851403 / 1694068.71 / 2913 / 86861；batch=original；weekdays 6 组（0..5，区间内无周六记录，与 SQLite 一致）
    orders total=2913（前 5 行）；/api/products?q=heart → 50 行；ece_original transactions=1,067,371
[3] 上传 sample.xlsx → succeeded（raw_rows=4, positive_sale_rows=4, 2011-02-01~02-05）
    批次库 db_path=mysql:ece_batch_b20261029T080955eadf90（transactions=4, sale=1: 4），未自动切换
    同文件再传 → duplicate（同一 batch_id）
    激活 → dashboard batch=上传批次，quantity=14 / revenue=24.50 / orders=3；orders total=3
    切回 original → batch=original，quantity=851403，orders=2913
[4] POST /api/forecast 202 → succeeded；origin=2011-12-09，horizon=7，rows=7，actual_days=0；CSV 导出 200 text/csv（1076 bytes）
[5] 模拟库存：item 参数后 balance=100、candidate=10；+20 → 120；−30 → 90；同幂等键再提交 → duplicate=true 仍 90；
    出库 91 → 400 且余额仍 90；流水 2 条；确认在途 10 → balance 90、effective_in_transit=10、补货候选 = max(0,80+30−90−10)=10
[6] /api/ai/status → llm_configured=false, fallback=local-rule-planner, tools_count=7；
    /api/ai/ask「2011-11-10 到 2011-12-09 的销量是多少？」→ 200，答案含 851403 件 / £1694068.71（本地规则回退，数字来自服务端工具）
[7] ece_app_test 落库核对：users=2, batches=2, scenarios=1, movements=2, jobs=1, ai_runs=1；original.db_path=mysql:ece_original
结果: PASS（全部检查通过）
[cleanup] 已删除 ece_app_test 与本次新增的 1 个批次库
```

另用**默认 runtime + runtime/mysql.env**（不覆盖任何 `ECE_*`）做了一次真实启动冒烟：

```
cd 电商运营系统
python run.py --port 8791
登录（runtime/initial_admin.txt 的 admin 口令）→ 200
/api/health → {"ok": true, "data_ready": true, "batch": original 原始数据 2009-12-01~2011-12-09}
/api/dashboard?start=2011-11-10&end=2011-12-09 → quantity 851403, revenue 1694068.71, orders 2913, lines 86861
/api/orders → total 2913；/api/products?q=heart → 50 行
```

## 7. SQLite 模式回归

### 7.1 服务层 16 项

```powershell
cd 03_测试与验证\系统测试
$env:ECE_DB_BACKEND='sqlite'; python -m unittest discover -s tests -p test_services.py -v
Ran 16 tests in 6.524s — OK
```
另一种方式（**不设** `ECE_DB_BACKEND`，仅隔离 `ECE_RUNTIME_DIR`，无 mysql.env → 自动 SQLite）：
`Ran 16 tests in 10.620s — OK`。

### 7.2 API 58 项

```powershell
cd 03_测试与验证\系统测试
python tests_api\run_api_tests.py --out mysql_regression.log `
  --label "MySQL 迁移后 SQLite 模式回归（ECE_DB_BACKEND 未设置，隔离 ECE_RUNTIME_DIR）"
Ran 58 tests in 77.227s — OK        # 58/58 通过
用例: 共 58，失败 0，错误 0，跳过 0
官方库是否被修改: 否（retail.sqlite3 / app.sqlite3 运行前后 sha256 一致）
```

过程说明（如实记录）：第一次跑 58 项时出现 1 项失败
`test_contract.test_05_http_status_codes_are_documented`——`GET /api/definitely-not-a-route` 返回 200 HTML 而非 404 JSON，
原因是 `frontend/dist` 已存在时 `api/main.py` 的 SPA 兜底 `@app.get('/{spa_path:path}')` 也吞掉了 `/api/*`。
该路由属 Lead 的静态层改动，与数据层/方言层无关（失败发生在路由兜底，未触达数据库）。已反馈 Lead，
Lead 在 `spa_fallback` 中增加 `if spa_path == 'api' or spa_path.startswith('api/'): return 404 JSON` 后，复跑即 58/58。
两次运行日志均在 `03_测试与验证/系统测试/runs/20261029_prototype/mysql_regression.log`（后者为最终版），副本 `.tmp/mysql_regression.log`。

### 7.3 SQLite 行为保持不变的证据

- `ECE_DB_BACKEND` 未设且无 mysql.env 时 `db.db_backend()=='sqlite'`，`connect()` 走原 `sqlite3.connect` 分支（PRAGMA、Row、文件库路径均不变）；
- `batches.db_path` 仍存 `runtime/retail.sqlite3` 这类相对路径；批次库仍是 `runtime/batches/<id>/data.sqlite3`；
- 上传/导入/预测/库存/认证 的 SQL 与返回口径未改（仅 weekday 表达式按后端二选一、`count(*) lines` 在 MySQL 侧加反引号）；
- `api/services/batches.py` 中 `original` 批次在 SQLite 模式仍按 `runtime/retail.sqlite3` 登记并读取 `data_manifest.json`。

## 8. 已知限制（如实记录）

1. **换机需重装/重新初始化**：`.mysql/`（约 1.5 GB 程序）与 `runtime/mysql-data/`（数据）都不入版本库；
   新机需先下载 MySQL 8 winx64 zip 到 `.tmp/mysql/`，再跑 `mysql_init.ps1`（约 2—3 分钟解压+初始化）和 `migrate_to_mysql.py`（约 2 分钟）。
   老师/同学机器也可用官方安装包，只需把 `ECE_MYSQL_*` 指向自己的实例（端口可改）。
2. **端口固定 3307**、仅 `127.0.0.1`；若 3307 被占用，改 `.mysql/my.ini` 的 `port` 与 `runtime/mysql.env` 的 `ECE_MYSQL_PORT`。
3. **口令文件位置**：`runtime/mysql.env`（含 `ECE_MYSQL_PASSWORD`）与 `runtime/initial_admin.txt`，均被 `.gitignore` 的 `runtime/` 覆盖；
   交付包如整体复制 `runtime/`，需确认这两份文件随包走但**不要进入代码仓库**。重置口令：`python scripts/mysql_bootstrap.py --reset-password`。
4. **项目路径含中文 + Windows + MySQL 8** 需 ANSI 写 `my.ini`（脚本已处理）；若路径换成纯 ASCII 同样可用。
5. **进程生命周期**：MySQL 不是系统服务，需 `mysql_start.ps1` 手动/脚本启动；在本沙箱内须由长驻后台作业托管（见 §3 踩坑 2）。
   正式演示建议开机后先跑一次 `mysql_start.ps1`（`启动系统.cmd` 里串联即可）。
6. **MySQL 类型差异**：聚合 `SUM()` 返回 `Decimal`，`quantity`/`revenue` 在 JSON 中是数值（`851403.0` 等）；
   四舍五入口径与 SQLite 版一致（金额以 `amount_units` 整数/10000 计算，未用 MySQL 浮点累加）。SQLite 模式仍是整数/浮点原样。
7. **READ COMMITTED**：应用连接隔离级别为 READ COMMITTED（对齐 SQLite 单写者语义），MySQL 全局默认仍是 REPEATABLE READ。
8. **批次库命名**：`ece_batch_<batch_id>`（batch_id 含时间戳，库名长度 < 64）；测试脚本会清理自己新建的批次库，`ece_app_test` 只在验收时存在。
9. SQLite 与 MySQL 的 `rowid`：MySQL 侧 `inventory_movements/inventory_in_transit` 显式加了自增 `rowid` 列以兼容 `order by rowid`，
   `select *` 会多出该列（API 只挑选字段返回，不对外暴露）。

## 9. 交付给 Lead 的配置项（我未改这些文件）

- `启动系统.cmd` / `启动系统.ps1`：若存在 `runtime\mysql.env`，先执行
  `powershell -ExecutionPolicy Bypass -File "%~dp0scripts\mysql_start.ps1"`（失败给出提示后继续或退出），再启动 `python run.py`；
  可另提供 `stop_mysql.cmd` 调 `mysql_stop.ps1`。
- `.env.example`（新增/说明，键名与我实现一致）：

```ini
# 默认 sqlite（测试/回退）；演示与交付用 mysql。runtime/mysql.env 存在时默认 mysql。
ECE_DB_BACKEND=sqlite
ECE_MYSQL_HOST=127.0.0.1
ECE_MYSQL_PORT=3307
ECE_MYSQL_USER=ece
ECE_MYSQL_PASSWORD=<由 scripts/mysql_init.ps1 写入 runtime/mysql.env，勿提交>
ECE_MYSQL_APP_DB=ece_app
ECE_MYSQL_ORIGINAL_DB=ece_original
```
- `requirements.txt`：`PyMySQL>=1.1`（Lead 已加；本机 `.pydeps/pymysql` 为 1.2.3 副本，正常机器 `pip install -r requirements.txt` 即可）。
- 说明：**不要**在 `.env` 里硬编码口令；`runtime/mysql.env` 已由脚本生成并在 `.gitignore` 内。

## 10. 复现命令（从零到验收）

```powershell
# 1) 便携 MySQL：解压 + 初始化 + 启动 + 建账号/库/写 runtime\mysql.env
powershell -ExecutionPolicy Bypass -File 电商运营系统\scripts\mysql_init.ps1
# 2) 数据迁移（SQLite → MySQL，含逐项核对，退出码 0 才算通过）
cd 电商运营系统; python scripts\migrate_to_mysql.py
# 3) MySQL 模式验收（独立 ece_app_test，自动清理）
python scripts\acceptance_mysql.py
# 4) 正式启动（runtime/mysql.env 存在 → 自动 MySQL）
python run.py --port 8777
# 5) SQLite 回退 / 回归
$env:ECE_DB_BACKEND='sqlite'; python run.py            # 或临时改名 runtime\mysql.env
cd ..\03_测试与验证\系统测试
$env:ECE_DB_BACKEND='sqlite'; python -m unittest discover -s tests -p test_services.py -v
python tests_api\run_api_tests.py --out mysql_regression.log
```
