# 智能电商运营管理系统

FastAPI + MySQL 8 + Vue 3。包含登录、导入、看板与订单、全店预测、模拟库存与补货、只读AI查询及简报。

## 启动

双击根目录的“启动系统.cmd”，自动启动本机MySQL和后端并打开[系统页面](http://127.0.0.1:8777/)。

终端启动且不自动打开浏览器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/启动系统.ps1 -NoBrowser
```

后端端口8777，MySQL端口3307，仅监听本机。现有MySQL数据仍位于runtime/mysql-data。

## 配置

本机实际配置保存在`config/local/`，该目录不进入版本管理，也不会上传到云仓库。其他电脑或云环境需要单独准备运行配置；不要把真实账号、口令或API密钥提交到仓库。真实AI未配置时，页面明确使用本地只读规则工具。

## 文件入口

| 位置 | 用途 |
|---|---|
| backend/app | 应用、接口、统一业务服务与数据库实现 |
| backend/tests | 服务测试、API测试、MySQL集成验收 |
| backend/run.py | 正式后端启动入口 |
| backend/cli.py | 数据准备、独立预测、历史回测及服务命令 |
| frontend | Vue源码、构建配置与dist产物 |
| config/local | 本机实际配置与登录信息 |
| docs | [API契约](docs/API契约.md) |
| scripts | 唯一启动脚本与MySQL工具 |
| runtime | 运行数据、模型、批次、日志与临时文件 |
| vendor | 便携MySQL及本机内置Python依赖 |

共享原始Excel仍在上级“20261014_电商数据与页面选型”目录；正式报告、实验与验证证据按上级总索引查找。旧看板归档在上级03_测试与验证/历史实现/旧版看板，不参与正式启动。

## 开发与验证

Python依赖：`pip install -r backend/requirements.txt`；本机可复用vendor/python-packages。

在frontend目录执行`npm run type-check`及`npm run build-only -- --configLoader native`，不手改dist。

从本系统根目录运行：

```powershell
python -m unittest discover -s backend/tests/services -p test_services.py -v
python backend/tests/api/run_api_tests.py --out test_api.log
```

测试使用隔离运行目录；证据在上级03_测试与验证/系统测试/runs。数据库工具位于scripts/mysql；已有数据时不要使用强制初始化。

业务只使用robust_blend（daily-v3-robust-blend-mad3），统计和预测口径沿用原实现。销售额单位GBP，不等于利润。商品需求参考历史销量，库存和采购流水为模拟，全店预测不自动分摊成商品补货量。
