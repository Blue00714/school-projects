# API 自动化测试套件（tests_api）

FastAPI 原型接口层自动化测试。**只用标准库**（`unittest` + `urllib` + `http.cookiejar`），
不引入 pytest/httpx/requests。

## 运行

```powershell
# 在项目根目录（C:\Users\Administrator\Desktop\学校项目）
python "电商运营系统/backend/tests/api/run_api_tests.py" --out test_api.log

# 只跑某个文件（调试用；日志名避免覆盖正式证据）
python "电商运营系统/backend/tests/api/run_api_tests.py" --pattern "test_auth.py" --out test_api_probe_auth.log

# 关键验收数字（写 acceptance_prototype.json）
python "电商运营系统/backend/tests/api/build_acceptance.py"
```

等价的原生 discover 方式（结果不落证据文件）：

```powershell
python -m unittest discover -s "03_测试与验证/系统测试/tests_api" -t "03_测试与验证/系统测试/tests_api" -p "test_*.py" -v
```

## 隔离方式

- 每个测试类在 `setUpClass` 启动一个子进程 `python run.py --port <OS 分配的空闲端口>`，
  `tearDownClass` 中 terminate/kill，端口不固定、可重复运行。
- 注入独立环境：`ECE_RUNTIME_DIR`（`.tmp/qa_api/<类名>-<token>/runtime`）、`ECE_APP_DB`、
  `ECE_ORIGINAL_DB`（复制 `电商运营系统/runtime/retail.sqlite3` 到该临时目录）、
  `ECE_ADMIN_PASSWORD` / `ECE_OPERATOR_PASSWORD`（≥8 位固定测试口令）、`ECE_SECRET_KEY`。
- 正式 `电商运营系统/runtime` 只读取（复制）不写入；`run_api_tests.py` 在运行前后对
  `retail.sqlite3` / `app.sqlite3` 计算 SHA-256 并写入日志：
  **本轮记录为「官方库是否被修改：否」**。
- 每个类结束后删除临时库副本（保留 `server.log` 便于诊断）；`QA_KEEP_TMP=1` 可全部保留。

## 覆盖范围

| 文件 | 覆盖 |
|---|---|
| `test_auth.py` | health、登录成功/失败、Cookie 属性、me、未登录 401、operator 403、admin 用户管理、改密撤销其它会话、退出后 401 |
| `test_import.py` | 错误类型/缺列失败、CSV+XLSX 上传、异步任务、质量报告、不自动切换、duplicate、显式切换→看板/订单跟随、长编号/前导零/公式注入、切回原始 |
| `test_orders.py` | 订单 total=看板 orders、分页合计=基线、明细相加=订单头、排序、长编号字符串、筛选一致、q 搜索、404 |
| `test_dashboard.py` | 851403/1694068.71/2913/86861、趋势 30 天（含 4 个无记录星期日补 0）、前期对比、排名/国家/星期/质量、默认区间、三类 CSV、400、空范围、商品/交易 |
| `test_forecast.py` | 7 行且 actual 全 null、任务字段与持久化、CSV 11 列、模型选择、回测 12 起点/84 预测、任务批次绑定跨切换、400/404、防未来泄漏（直接复用既有算法思路） |
| `test_inventory.py` | 场景、参数、100+20-30=90、幂等、出库 91 拒绝、补货候选 10、在途确认与到货、`simulated:true`、校验/404 |
| `test_ai.py` | 未配置状态、只读工具声明、30 题抽样（结构+数字一致性）、澄清追问、无利润编造、简报生成/下载/404、密钥不泄露 |
| `test_contract.py` | openapi 与实际路由一致、契约 43 端点全部实现、错误体/错误码、同源 403、页面/静态资源由 FastAPI 提供 |

## 已知测试侧限制（不是产品结论）

- AI 数字核对采用"批准集合"：看板（含国家筛选）、订单首页、商品检索、当前批次 manifest；
  若实现改为引用第 2 页之后的订单编号，会误报——需同步放宽（当前实现首页 10 笔）。
- 30 题 AI 是固定抽样，不是完整人工审核题集；真实模型需另做验收。
- 页面截图不在本套件内（由前端组浏览器验收提供）。
