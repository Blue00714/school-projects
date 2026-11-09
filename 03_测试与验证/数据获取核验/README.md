# 数据下载与基础核验

共享数据保留原位：[online_retail_II.xlsx](../../20261014_电商数据与页面选型/online_retail_II.xlsx)、[数据说明](../../20261014_电商数据与页面选型/数据说明.txt)。

- [下载与核验脚本](download_dataset.py)、[原下载记录](download_record.json)、[原运行日志](download_run.log)。
- online_retail_ii.zip.part是旧官方连接失败的下载片段，完整保留作过程材料，不是可使用的数据交付。
- 脚本依赖Python、openpyxl及网络；原始Excel仍写入共享原料目录，核验记录、压缩包和下载片段写入本包。

复现时从本目录运行`python download_dataset.py --mirror`；会访问网络或重新核验，不属于本次整理。旧记录中的绝对路径是当时位置，当前地址以上方链接为准。镜像来源与官方原文件逐字节核对状态均不改变。
