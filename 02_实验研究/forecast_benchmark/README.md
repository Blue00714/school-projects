# 日销量预测实验

2026-11-03：[单模型预测与补货演示完善](20261103_单模型与补货完善/README.md)。业务端已收起所有模型选项，固定部署验证过的稳健预测流程；旧候选与旧实验保留作复现依据。

2026-11-02续做：[预测调优与末端46.1%误差诊断](20261102_预测调优/README.md)。采用系统规范数据口径，结果与本目录旧实验分别保留。

主要报告：[结果摘要](结果摘要.md)。保留历史方法比较，不在本次整理中重训、重算或修正历史结论。

| 内容 | 位置 |
|---|---|
| 专用实验脚本 | [run_benchmark.py](run_benchmark.py) |
| 结果表与日志 | [results](results/) |
| 实验图 | [figures](figures/) |
| 日序列缓存 | [data/daily_qty.csv](data/daily_qty.csv) |
| 共享原始Excel | [原数据](../../20261014_电商数据与页面选型/online_retail_II.xlsx) |

依赖：Python、pandas、numpy、scikit-learn、lightgbm、matplotlib；原脚本对statsmodels的使用按原实现保留。输出继续位于本实验包的data、results、figures内。

复现时从本目录运行`python run_benchmark.py`；该操作会重新计算，不属于本次整理。旧实验与系统服务的数据处理口径分别保留，不合并结果。
