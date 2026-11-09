# 本轮开发/验收的补充证据（support）

这些文件原本散落在开发用的临时目录 `.tmp/` 中（该目录不入交付、且含数百 MB 运行库副本，已清理）。为保留可复核的脚本与原始输出，把其中的**小体积证据**归档到这里；各模块 notes 中提到的 `.tmp/...` 路径对应本目录下的同名/前缀文件。

| 文件 | 来源 | 用途 |
|---|---|---|
| `ai_run_accept.py`、`ai_acceptance_result.json`、`ai_question_items.json` | M5 AI 验收脚本与 30 题结果 | 复现 AI 30 题固定集（需自行起隔离服务） |
| `fc_http_selftest.py`、`fc_http_selftest.log`、`fc_evidence_dump.json` | M3/M4 预测与库存自测 | 50 项接口断言与原始输出 |
| `auth_selftest.py`、`auth_selftest_output.txt` | M1 认证自测 | 86 项断言原始输出 |
| `m2_probe*.py/json`、`m2_sample_upload.csv/.xlsx`、`m2_fake.xls`、`m2_bad_columns.csv`、`m2_empty.csv` | M2 导入验收样例与探测 | 导入质量、判重、错误路径的可复用样例 |
| `qa_probe_*.py`、`qa_routes.py`、`qa_smoke.py` | QA 早期探测脚本 | 联调过程记录 |
| `install_wheels.py` | 依赖内置脚本 | 受限环境无 pip 时，从镜像下载固定 wheel 解包到 `.pydeps/` 的可复现步骤 |

说明：这些是开发过程证据，不是正式测试入口；正式测试入口是 `../test_api.log` 与 `tests_api/`。`m2_large_upload.csv`（7.5MB）为生成的大文件样例，未归档，可用脚本重新生成。
