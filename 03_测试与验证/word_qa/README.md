# 报告生成与版式验证

交付文件：[前三章Word](../../01_交付内容/基于FastAPI的智能电商运营管理系统_前三章参考稿.docx)。本目录内的DOCX均为过程版本，不作为最新交付。

- [原构建与验收记录](artifact.md)、[构建清单](build_manifest.json)。
- 最新保存的版式证据：[revision_verified.pdf](revision_verified.pdf)、[逐页PNG](revision-verified-render/)。其余PDF和渲染目录保留为历史过程，不混入交付目录。
- 修订依赖：revision_before.docx、revision_content.json、revise_report.py、revision_figures.py、build_report.py中的绘图帮助函数；再经revise_render.ps1与finalize_revision.py处理。
- 原学校模板仍在原微信文件目录；复现依赖docx、Pillow、lxml、pypdf、原生Word兼容COM及Poppler，具体以artifact.md历史记录为准。

2026-10-22仅调整位置与脚本路径，没有执行上述流程。revise_report.py等续修入口指向交付目录；旧版build_report.py主入口改为本目录的“旧版重新生成_前三章参考稿.docx”，避免覆盖当前版本。历史任务文本保留原样。
