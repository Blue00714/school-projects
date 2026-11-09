# M1（api-auth）实现与自测记录

- 模块负责人：api-auth
- 契约版本：`电商运营系统/API契约.md` v0.2.0-prototype（第 1、2、3、5.1、5.2、6 节）
- 时间：2026-10-29（本机 Asia/Shanghai；服务内时间戳一律 UTC，用 `api.security.utc_now()`）
- 结论：**8 项任务全部实现；自测 86 项断言全部通过（86 passed / 0 failed）**，本记录为当轮实际运行输出。

## 1. 改动文件（仅本模块作用域）

| 文件 | 变更 |
|---|---|
| `电商运营系统/api/routers/auth.py` | 新建实现：`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`、`POST /api/auth/password` |
| `电商运营系统/api/routers/system.py` | 新建实现：`GET /api/health`、`GET /api/options`、`GET/POST /api/users`、`PATCH /api/users/{user_id}`、`GET/PUT /api/settings` |
| `电商运营系统/api/services/users.py` | 新建：账号/会话/审计服务层（校验、写库、自我保护规则、token 摘要存储） |
| `03_测试与验证/系统测试/runs/20261029_prototype/api_auth_notes.md` | 本记录 |

未改动 Lead 所有的核心文件（`api/__init__.py`、`config.py`、`db.py`、`security.py`、`deps.py`、`errors.py`、`api/services/batches.py`、`api/main.py`、`run.py` 等），未改动 `services/`（非本模块文件）、`web/`、`20261014_电商数据与页面选型/online_retail_II.xlsx`、`runtime/retail.sqlite3`。自测服务日志首行可证：`startup complete; app db=C:\...\.tmp\rt_auth\app.sqlite3`（未写正式 `runtime/app.sqlite3`）。

## 2. 环境与启动命令（隔离运行目录，避免污染正式库）

- 解释器：系统 Python 3.12.10；`.pydeps` 内置 FastAPI 0.115.6 / Starlette 0.41.3 / pydantic 2.10.4（`api/__init__.py` 自动加入 `sys.path`）
- 隔离目录：`.tmp/rt_auth`；原始数据批次只读复用 `电商运营系统/runtime/retail.sqlite3`
- 首次启动自动生成一次性口令：`.tmp/rt_auth/initial_admin.txt`（UTF-8，测试脚本从此读取，未写入任何文档）

```powershell
$env:ECE_RUNTIME_DIR='C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_auth'
$env:ECE_ORIGINAL_DB='C:\Users\Administrator\11111\数据\学校项目\电商运营系统\runtime\retail.sqlite3'
$env:ECE_APP_DB='C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_auth\app.sqlite3'
cd 'C:\Users\Administrator\11111\数据\学校项目\电商运营系统'
python run.py --port 8781
```

服务端启动日志（节选，无 5xx、无未处理异常）：

```
INFO:     Started server process [28820]
INFO:     Waiting for application startup.
2026-10-29 15:02:53,832 INFO ece.main startup complete; app db=C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_auth\app.sqlite3
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8781 (Press CTRL+C to quit)
```

## 3. 自测命令

自测脚本用标准库 `urllib` + `http.cookiejar`（与契约第 6 节测试约定一致，不引入 pytest/httpx），临时放在 `.tmp/auth_selftest.py`（未放进正式目录；qa 可直接迁入 `tests_api/`）：

```powershell
cd 'C:\Users\Administrator\11111\数据\学校项目'
python .tmp\auth_selftest.py --base http://127.0.0.1:8781 `
  --app-db 'C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_auth\app.sqlite3' `
  --initial-admin 'C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_auth\initial_admin.txt'
```

退出码 `0`；完整输出如下（`PASS` 行即断言证据，方括号内为实际响应片段）。

## 4. 自测输出（真实运行结果，原样粘贴）

```
PASS health 200 and login-free
PASS health fields complete  [ai_configured,batch,data_ready,ok,service,version]
PASS health service/version
PASS health data_ready true (Store constructible)
PASS health batch public
PASS health ai_configured is bool
PASS GET /api/auth/me without login -> 401  [401 {'error': '未登录或登录已失效', 'code': 'unauthorized'}]
PASS GET /api/options without login -> 401  [401 {'error': '未登录或登录已失效', 'code': 'unauthorized'}]
PASS login wrong password -> 401 unified message  [401 {'error': '账号或密码错误', 'code': 'unauthorized'}]
PASS login unknown user -> 401 unified message  [401 {'error': '账号或密码错误', 'code': 'unauthorized'}]
PASS admin login 200  [200 {'user': {'id': 1, 'username': 'admin', 'display_name': '系统管理员', 'role': 'admin', 'active': True, 'must_change_password': True}}]
PASS login user object fields  [active,display_name,id,must_change_password,role,username]
PASS login user role admin and no password hash
PASS Set-Cookie ece_session present  [ece_session=HSZp66tm94f3j7YMYRf3MjPNErBjSzzWZjyUCpLI-4E; HttpOnly; Max-Age=43200]
PASS cookie HttpOnly/Path//SameSite=Lax  [ece_session=HSZp66tm94f3j7YMYRf3MjPNErBjSzzWZjyUCpLI-4E; HttpOnly; Max-Age=43200; Path=/; SameSite=lax]
PASS cookie Max-Age=43200 (12h)  [ece_session=HSZp66tm94f3j7YMYRf3MjPNErBjSzzWZjyUCpLI-4E; HttpOnly; Max-Age=43200; Path=/; SameSite=lax]
PASS cookie value not empty
PASS GET /api/auth/me 200  [200 {'user': {'id': 1, 'username': 'admin', 'display_name': '系统管理员', 'role': 'admin', 'active': True, 'must_change_password': True}}]
PASS seeded admin must_change_password true
PASS GET /api/options 200  [200]
PASS options existing contract keys
PASS options batch + forecast extras  [{"batch": {"id": "original", "label": "原始数据 2009-12-01~2011-12-09", "kind": "original", "status": "ready", "is_active": true, "source_file": "online_retail_II.xlsx", "source_sha256": "bcbe73b35f5b7bab]
PASS options products <=500 with stock_code/name/quantity  [500]
PASS options countries non-empty list
PASS options methods = 5 forecasting methods
PASS options manifest has policy_version/date range
PASS options second call served from batch cache  [first=1725ms second=28ms]
INFO options first=1725ms cached=28ms
PASS operator login 200  [200 {'user': {'id': 2, 'username': 'operator', 'display_name': '运营人员', 'role': 'operator', 'active': True, 'must_change_password': True}}]
PASS operator GET /api/users -> 403  [403 {'error': '该操作需要管理员权限', 'code': 'forbidden'}]
PASS operator GET /api/settings -> 403  [403 {'error': '该操作需要管理员权限', 'code': 'forbidden'}]
PASS operator POST /api/users -> 403  [403 {'error': '该操作需要管理员权限', 'code': 'forbidden'}]
PASS operator PATCH /api/users/1 -> 403  [403 {'error': '该操作需要管理员权限', 'code': 'forbidden'}]
PASS operator PUT /api/settings -> 403  [403 {'error': '该操作需要管理员权限', 'code': 'forbidden'}]
PASS admin GET /api/users 200  [200 2 users]
PASS users list has no password_hash
PASS admin POST /api/users -> 201  [201 {'user': {'id': 3, 'username': 'qa_user', 'display_name': 'QA测试账号', 'role': 'operator', 'active': True, 'must_change_password': False}}]
PASS created user has no password_hash
PASS duplicate username -> 409 conflict  [409 {'error': '用户名已存在', 'code': 'conflict'}]
PASS password shorter than 8 -> 400  [400 {'error': '密码至少8位', 'code': 'bad_request'}]
PASS invalid role -> 400  [400 {'error': '角色必须为 admin 或 operator', 'code': 'bad_request'}]
PASS created user can log in  [200 {'user': {'id': 3, 'username': 'qa_user', 'display_name': 'QA测试账号', 'role': 'operator', 'active': True, 'must_change_password': False}}]
PASS created operator GET /api/users -> 403  [403 {'error': '该操作需要管理员权限', 'code': 'forbidden'}]
PASS admin PATCH deactivate 200  [200 {'user': {'id': 3, 'username': 'qa_user', 'display_name': 'QA测试账号', 'role': 'operator', 'active': False, 'must_change_password': False}}]
PASS disabled account login -> 401 unified message  [401 {'error': '账号或密码错误', 'code': 'unauthorized'}]
PASS disabled account existing session -> 401  [401 {'error': '未登录或登录已失效', 'code': 'unauthorized'}]
PASS cannot deactivate self -> 400  [400 {'error': '不能停用当前登录的账号', 'code': 'bad_request'}]
PASS cannot demote self -> 400  [400 {'error': '不能取消自己的管理员角色', 'code': 'bad_request'}]
PASS PATCH unknown user -> 404  [404 {'error': '账号不存在', 'code': 'not_found'}]
PASS admin PATCH password+active 200  [200 {'user': {'id': 3, 'username': 'qa_user', 'display_name': 'QA测试账号', 'role': 'operator', 'active': True, 'must_change_password': False}}]
PASS login with admin-reset password 200  [200 {'user': {'id': 3, 'username': 'qa_user', 'display_name': 'QA测试账号', 'role': 'operator', 'active': True, 'must_change_password': False}}]
PASS operator second session login 200
PASS password change wrong old -> 400  [400 {'error': '旧密码不正确', 'code': 'bad_request'}]
PASS password change new too short -> 400  [400 {'error': '密码至少8位', 'code': 'bad_request'}]
PASS password change 200 {ok:true}  [200 {'ok': True}]
PASS current session kept after password change  [200]
PASS other sessions revoked after password change  [401]
PASS old password no longer works  [401 {'error': '账号或密码错误', 'code': 'unauthorized'}]
PASS new password works  [200 {'user': {'id': 2, 'username': 'operator', 'display_name': '运营人员', 'role': 'operator', 'active': True, 'must_change_password': False}}]
PASS logout 200 {ok:true}  [200 {'ok': True}]
PASS logout clears cookie  [ece_session=""; expires=Thu, 29 Oct 2026 07:03:15 GMT; HttpOnly; Max-Age=0; Path=/; SameSite=lax]
PASS after logout /api/auth/me -> 401  [401 {'error': '未登录或登录已失效', 'code': 'unauthorized'}]
PASS revoked token replayed after logout -> 401  [401 {'error': '未登录或登录已失效', 'code': 'unauthorized'}]
PASS unknown session token -> 401  [401 {'error': '未登录或登录已失效', 'code': 'unauthorized'}]
PASS admin GET /api/settings 200  [200]
PASS settings ai public shape
PASS settings session_ttl_hours 12
PASS settings batch original
PASS PUT /api/settings 200 with stored values  [200 {'configured': False, 'provider': 'openai-compatible', 'base_url': 'https://llm.example.com/v1', 'model': 'demo-model', 'timeout_seconds': 45.0, 'key_present': False}]
PASS settings never returns API key
PASS PUT api_key rejected -> 400  [400 {'error': 'AI设置仅允许 base_url、model、timeout_seconds；API密钥只能通过环境变量配置，接口不接受密钥', 'code': 'bad_request'}]
PASS PUT invalid timeout -> 400  [400 {'error': 'timeout_seconds 需在1至600秒之间', 'code': 'bad_request'}]
PASS PUT invalid base_url -> 400  [400 {'error': 'base_url 必须以 http:// 或 https:// 开头', 'code': 'bad_request'}]
PASS PUT unknown ai key -> 400  [400 {'error': 'AI设置仅允许 base_url、model、timeout_seconds；API密钥只能通过环境变量配置，接口不接受密钥', 'code': 'bad_request'}]
PASS stored settings persisted for later GET  [{"configured": false, "provider": "openai-compatible", "base_url": "https://llm.example.com/v1", "model": "demo-model", "timeout_seconds": 45.0, "key_present": false}]
PASS PUT /api/settings empty body 200 keeps stored values  [200]
PASS PUT null clears stored override (falls back to env)  [{"configured": false, "provider": "openai-compatible", "base_url": "https://llm.example.com/v1", "model": "", "timeout_seconds": 45.0, "key_present": false}]
PASS PUT top-level unknown field -> 400  [400 {'error': '参数无效：api_key Extra inputs are not permitted', 'code': 'bad_request'}]
PASS sessions rows present  [8 sessions]
PASS session token stored as sha256 hex (64 chars, no raw token)
PASS raw token not stored in sessions table
PASS audit log records user.create/user.update/auth.password_change/settings  [auth.password_change,settings.ai.update,user.create,user.update]
PASS options cache invalidated after batch switch  [200]
PASS options back to original batch after restore  [200]
PASS health still login-free 200  [200]
INFO operator password was changed during this run (isolated runtime only)
PASS last enabled admin cannot be removed (unit) -> 400  [系统必须保留至少一个启用状态的管理员]
PASS admin account still active after blocked change

===== 86 passed, 0 failed =====
```

## 5. 契约对照（逐条）

| 任务 | 实现 | 验证证据 |
|---|---|---|
| 1 `POST /api/auth/login` | `security.verify_password` 校验；成功写 `sessions`（token 只存 `security.token_digest` SHA-256）；`Set-Cookie: ece_session=...; HttpOnly; Path=/; SameSite=Lax; Max-Age=SESSION_TTL_HOURS*3600`；返回 `{user}`；账号不存在/密码错/停用统一 401「账号或密码错误」 | 输出第 9–17 行；Cookie 实际串 `HttpOnly; Max-Age=43200; Path=/; SameSite=lax` |
| 2 `POST /api/auth/logout` | 撤销当前会话（`sessions.revoked=1`）+ 清 Cookie，200 `{ok:true}`；无 Cookie 也幂等返回 200 | 第 60–64 行；另用旧 token 显式重放 Cookie 头仍 401（服务端撤销，不只是浏览器丢 Cookie） |
| 3 `GET /api/auth/me` | `Depends(current_user)`，返回 `{user}`；未登录 401 | 第 7、18 行 |
| 4 `POST /api/auth/password` | 校验旧密码；新密码 ≥8 位；更新 PBKDF2 哈希、`must_change_password=0`；撤销该用户其它会话、保留当前会话；200 `{ok:true}` | 第 52–59 行（同账号第二会话改密后变 401） |
| 5 `GET /api/health` | 免登录；`{ok,service:"ecommerce-ops-fastapi",version:"0.2.0-prototype",data_ready,batch,ai_configured}`；`data_ready` = 当前批次库存在且 `Store(path).manifest()` 可读（按 批次id+文件mtime+size 缓存）；`batch=deps.batch_public(active_batch())` | 第 1–6 行 + 第 5 节快照；无批次时 `data_ready=false, batch=null` 不抛错 |
| 6 `GET /api/options` | 契约既有 `{manifest,countries,products,methods}`（products ≤500、countries 同口径）+ `batch` + `forecast:{scope:"whole-shop",horizon:7}`；进程内按 active batch id 缓存，切换批次失效 | 第 20–28 行；首次 1725ms → 缓存 28ms；第 83–84 行验证切批次后缓存失效、切回后恢复 |
| 7 用户管理（`Depends(require_admin)`） | `GET /api/users`；`POST /api/users` 201（重名 409、密码<8 位 400、role 校验 400）；`PATCH /api/users/{id}`（active/role/password/display_name 任选；不能停用或降级自己；不能移除最后一个启用管理员 400）；写操作全部 `db.audit` | 第 30–51 行、第 82 行 audit 动作集合、第 87–88 行最后管理员保护 |
| 8 `GET/PUT /api/settings`（admin） | GET `{ai:公开配置,session_ttl_hours,batch}`；PUT 仅接受 `{ai:{base_url?,model?,timeout_seconds?}}` 并写 `settings` 表（`db.set_setting('ai',...)`），**不接受、不返回** API key（顶层/嵌套多余字段均 400） | 第 65–78 行；`settings` 行实际为 `{"ai": "{\"base_url\": \"https://llm.example.com/v1\", \"timeout_seconds\": 45.0, \"model\": \"demo-model\"}"}` |

## 6. 关键实现说明

- 会话：`sessions(token_hash, user_id, created_at, expires_at, revoked, user_agent)`，时间全部 UTC ISO 秒级；Cookie 里是原始 token，库里只有 SHA-256（64 位十六进制）。登录失败对不存在的账号也走一次 PBKDF2（等时假哈希），避免用响应时间枚举账号。
- 权限：登录中间件在 `api/main.py`（Lead），路由内再用 `Depends(current_user)` / `Depends(require_admin)` 做二次校验；operator 调用任何管理接口均 403（第 30–34 行）。
- 自我保护：`PATCH` 时在同一事务内检查「不能停用自己」「不能把降级自己」「改后不能没有启用管理员」，失败 400 且事务回滚；审计写 `audit_log`（`user.create` / `user.update` / `auth.password_change` / `settings.ai.update`，不含任何口令）。
- options 缓存：`_OPTIONS_CACHE={'batch_id','payload'}`，仅当活跃批次 id 变化才重算；`deps.active_batch()` 每次请求读取，切换批次立即失效。
- data_ready 缓存：`(batch_id, db_path, mtime_ns, size)` 作键，批次库被替换（mtime/size 变化）自动重算。

## 7. 已知限制与说明（如实记录）

1. `/api/health` 的 `ok` 恒为 `true`（契约字面要求 `ok:true`）；数据是否就绪单独由 `data_ready` 表达，服务不可用时该端点本身 500 或连不上。
2. 「不能移除最后一个启用管理员」在纯 HTTP 层几乎不可达：唯一启用管理员就是操作者本人，会先命中「不能停用/降级自己」；操作他人时至少还留着自己。因此该规则用服务层直接调用 `user_service.update_user(admin_id, actor_id=None, active=False)` 断言（第 87–88 行），行为已验证为 400「系统必须保留至少一个启用状态的管理员」。
3. `/api/options` 的缓存失效验证在**隔离运行库**内插入了一个临时批次（`cache-test-batch`，指向同一零售数据文件）并切为活跃，请求后立即恢复/删除；未触碰 `runtime/app.sqlite3` 与 `runtime/retail.sqlite3`。
4. `PUT /api/settings` 存的是非敏感覆盖值（`settings.ai`），GET/PUT 返回「环境变量默认值 + 已存覆盖值」的合并结果，`configured` 仍要求 `key_present`（密钥只来自环境变量）。**AI 模块（ai-assistant）实际调用大模型目前读 `api/config.py` 的环境变量**；若要令设置页的 base_url/model 覆盖真正生效，ai-assistant 需读取 `db.get_setting('ai')` 并按同样规则合并——已同步给 Lead，本模块不擅自改他人文件。
5. 管理员新建账号的 `must_change_password=0`（管理员显式设定初始密码），初始种子账号为 `1`，用户自行改密后清零。契约未规定该初始值，此为约定；若产品要求「新建账号首次登录必须改密」，改一行即可。
6. 本轮只验证本模块端点；跨源非 GET 403、统一错误 JSON、路由注册等由 Lead 的 `api/main.py`/`api/errors.py` 负责，未在本文档重复记录。
7. 并行开发说明：本模块自测期间其他模块（analytics/imports/forecast/inventory/web 等）正在写各自文件；本模块只写自己 3 个文件（`auth.py`、`system.py`、`services/users.py`）。全量 `from api.main import app` 当时可导入（38 条 `/api` 路由）。
8. 临时产物：`.tmp/rt_auth/`（隔离运行库）、`.tmp/auth_selftest.py`、`.tmp/auth_selftest_output.txt`、`.tmp/auth_probe.py`、`.tmp/auth_probe.txt`。正式目录只新增本记录；qa 如需可把自测脚本迁入 `03_测试与验证/系统测试/tests_api/`。

## 8. 遗留问题

无阻塞项。待 Lead 决定的跨模块事项仅第 7 节第 4 条（settings 里的 AI 覆盖值是否由 ai-assistant 读取），不影响本模块验收。
