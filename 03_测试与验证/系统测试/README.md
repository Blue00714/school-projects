# 系统测试与验证

可复用测试代码集中在[backend/tests](../../电商运营系统/backend/tests/)。本目录保留各批次日志、截图、样例和验证证据。

从“电商运营系统”目录执行：

```powershell
python -m unittest discover -s backend/tests/services -p test_services.py -v
python backend/tests/api/run_api_tests.py --out test_api.log
```

- [服务测试](../../电商运营系统/backend/tests/services/test_services.py)
- [API测试说明](../../电商运营系统/backend/tests/api/README.md)
- [MySQL集成验收](../../电商运营系统/backend/tests/integration/acceptance_mysql.py)
- [历史验收结果](acceptance.json)
- [运行证据](runs/)
- [旧版看板](../历史实现/旧版看板/)

常规测试使用隔离目录，正式数据库不作为测试写入对象。历史日志中的旧路径保留原始语境，当前代码位置以上述链接为准。
