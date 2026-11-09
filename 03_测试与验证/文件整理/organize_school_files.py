"""One-time, non-destructive school-project file organization. No experiments run."""
from pathlib import Path
import ast
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'C:\Users\Administrator\Desktop\学校项目').resolve(strict=True)
DATA = ROOT / '20261014_电商数据与页面选型'
DELIVERY = ROOT / '01_交付内容'
EXPERIMENT = ROOT / '02_实验研究' / 'forecast_benchmark'
QA = ROOT / '03_测试与验证'
WORD = QA / 'word_qa'
TEST = QA / '系统测试'
DOWNLOAD = QA / '数据获取核验'
AUDIT = QA / '文件整理'
NAME = '基于FastAPI的智能电商运营管理系统_前三章参考稿.docx'

def bounded(p):
    p = p.resolve()
    if p == ROOT or ROOT not in p.parents:
        raise RuntimeError(f'Outside intended project: {p}')
    return p

def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def rel(p):
    return p.relative_to(ROOT).as_posix()

moves = [
    (DATA/NAME, DELIVERY/NAME),
    (DATA/'forecast_benchmark', EXPERIMENT),
    (DATA/'word_qa', WORD),
    (ROOT/'电商运营系统'/'tests', TEST/'tests'),
    (ROOT/'电商运营系统'/'runtime'/'test_tmp', TEST/'test_tmp'),
    (ROOT/'电商运营系统'/'runtime'/'screenshots', TEST/'screenshots'),
]
for name in ['acceptance.json','acceptance.log','cache_migration.log']:
    moves.append((ROOT/'电商运营系统'/'runtime'/name, TEST/name))
for name in ['tests_result.log','runtime_prepare.log','forecast_task.txt']:
    moves.append((ROOT/'电商运营系统'/name, TEST/name))
for name in ['download_dataset.py','download_record.json','download_run.log','online_retail_ii.zip.part']:
    moves.append((DATA/name, DOWNLOAD/name))

for src,dst in moves:
    bounded(src); bounded(dst)
    if not src.exists() or dst.exists():
        raise RuntimeError(f'Invalid source/destination: {src} -> {dst}')
for p in ROOT.rglob('*'):
    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):
        raise RuntimeError(f'Reparse point requires separate review: {p}')

before = {rel(p):digest(p) for p in ROOT.rglob('*') if p.is_file()}
AUDIT.mkdir(parents=True,exist_ok=True)
(AUDIT/'整理前文件校验.json').write_text(json.dumps(before,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
mapping=[]
for src,dst in moves:
    dst.parent.mkdir(parents=True,exist_ok=True)
    mapping.append({'from':rel(src),'to':rel(dst),'kind':'directory' if src.is_dir() else 'file'})
    shutil.move(str(src),str(dst))

edits=[]
def write(p,text):
    bounded(p)
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(text.rstrip()+'\n',encoding='utf-8')
    edits.append(rel(p))

def patch(p,old,new):
    text=p.read_text(encoding='utf-8-sig')
    if text.count(old)!=1:
        raise RuntimeError(f'Expected unique path expression in {p}: {old}')
    write(p,text.replace(old,new))

# Adjust only paths; do not execute report builders, downloader or benchmark.
patch(EXPERIMENT/'run_benchmark.py','PKG = ROOT.parent','PKG = ROOT.parents[1] / "20261014_电商数据与页面选型"')
patch(WORD/'revise_report.py','OUT = HERE.parent /', 'OUT = HERE.parents[1] / "01_交付内容" /')
patch(WORD/'finalize_revision.py',"OUT = Q.parent /", "OUT = Q.parents[1] / '01_交付内容' /")
patch(WORD/'revise_render.ps1',"'..\\基于FastAPI的智能电商运营管理系统_前三章参考稿.docx'", "'..\\..\\01_交付内容\\基于FastAPI的智能电商运营管理系统_前三章参考稿.docx'")
patch(WORD/'build_report.py','PACKAGE = ROOT.parent','PACKAGE = ROOT.parents[1] / "20261014_电商数据与页面选型"')
patch(WORD/'build_report.py',"OUTPUT = PACKAGE / '基于FastAPI的智能电商运营管理系统_前三章参考稿.docx'", "OUTPUT = ROOT / '旧版重新生成_前三章参考稿.docx'  # Legacy builder must not overwrite current delivery.")
patch(WORD/'finalize_revision.py',"'final_pdf':'word_qa/revision_final.pdf', 'final_page_images':'word_qa/revision-render-final'", "'final_pdf':'revision_final.pdf', 'final_page_images':'revision-render-final'")
patch(TEST/'tests'/'test_services.py','import tempfile','import tempfile\nimport sys\n\nSYSTEM_ROOT = Path(__file__).resolve().parents[3] / "电商运营系统"\nsys.path.insert(0, str(SYSTEM_ROOT))')
patch(TEST/'tests'/'test_services.py',"TEMP_ROOT=Path(__file__).resolve().parents[1]/'runtime'/'test_tmp'", "TEMP_ROOT=Path(__file__).resolve().parents[1]/'test_tmp'")
patch(TEST/'tests'/'verify_real.py','ROOT=Path(__file__).resolve().parents[1]', 'ROOT=Path(__file__).resolve().parents[3] / "电商运营系统"\nEVIDENCE_ROOT=Path(__file__).resolve().parents[1]')
patch(TEST/'tests'/'verify_real.py',"(ROOT/'runtime'/'acceptance.json').write_text", "(EVIDENCE_ROOT/'acceptance.json').write_text")
patch(DOWNLOAD/'download_dataset.py','ROOT = Path(__file__).resolve().parent','ROOT = Path(__file__).resolve().parents[2] / "20261014_电商数据与页面选型"\nEVIDENCE = Path(__file__).resolve().parent')
patch(DOWNLOAD/'download_dataset.py','ARCHIVE = ROOT / "online_retail_ii.zip"','ARCHIVE = EVIDENCE / "online_retail_ii.zip"')
patch(DOWNLOAD/'download_dataset.py','part = ROOT / "online_retail_ii.zip.part"','part = EVIDENCE / "online_retail_ii.zip.part"')
patch(DOWNLOAD/'download_dataset.py','part = ROOT / "online_retail_II.xlsx.part"','part = EVIDENCE / "online_retail_II.xlsx.part"')
patch(DOWNLOAD/'download_dataset.py','(ROOT / "download_record.json").write_text','(EVIDENCE / "download_record.json").write_text')

# Saved reports and raw evidence retain their contents. Add current navigation.
p=EXPERIMENT/'结果摘要.md'
t=p.read_text(encoding='utf-8')
t=t.replace('cd 20261014_电商数据与页面选型\\forecast_benchmark','cd 02_实验研究\\forecast_benchmark')
write(p,t)

p=DATA/'先看这里.md'
t=p.read_text(encoding='utf-8')
for old,new in [
    ('(基于FastAPI的智能电商运营管理系统_前三章参考稿.docx)','(../01_交付内容/'+NAME+')'),
    ('(download_dataset.py)','(../03_测试与验证/数据获取核验/download_dataset.py)'),
    ('(download_record.json)','(../03_测试与验证/数据获取核验/download_record.json)'),
    ('(download_run.log)','(../03_测试与验证/数据获取核验/download_run.log)'),
]:
    t=t.replace(old,new)
write(p,'# 资料与原始数据入口\n\n2026-10-22已按交付、实验、测试验证整理。当前总入口见[项目目录](../先看这里.md)。\n\n- [交付Word](../01_交付内容/'+NAME+')\n- [预测实验](../02_实验研究/forecast_benchmark/README.md)\n- [报告生成与版式验证](../03_测试与验证/word_qa/README.md)\n- [数据下载与核验](../03_测试与验证/数据获取核验/README.md)\n\n以下为原资料记录，日期、阶段状态及无链接的旧目录名保留为历史描述；不代表本次重新验证的状态。\n\n'+t)

write(ROOT/'先看这里.md','''# 学校项目文件入口

整理日期：2026-10-22。按交付、实验、测试验证分开；原始数据、系统源码及运行数据库保留原位。

| 类别 | 内容与入口 | 状态 |
|---|---|---|
| 交付文档 | [前三章Word](01_交付内容/基于FastAPI的智能电商运营管理系统_前三章参考稿.docx)、[交付说明](01_交付内容/README.md) | 现有参考稿；封面个人信息待填写 |
| 系统源码 | [电商运营系统](电商运营系统/README.md)、[启动看板](电商运营系统/启动看板.cmd) | 已有本地看板与预测代码；不将其称为完整系统已验收 |
| 预测实验 | [实验说明及结果](02_实验研究/forecast_benchmark/README.md) | 保留已有脚本、数据缓存、结果表、图与日志 |
| 测试与验证 | [验证总入口](03_测试与验证/README.md) | 报告版式、系统测试、数据核验分别存放 |
| 共享原始数据 | [Online Retail II Excel](20261014_电商数据与页面选型/online_retail_II.xlsx)、[数据说明](20261014_电商数据与页面选型/数据说明.txt) | 唯一共享原料，未搬迁、未改写 |
| 写作与任务资料 | [阶段任务](20261014_电商数据与页面选型/第一阶段任务与报告提纲.md)、[写作参考](20261014_电商数据与页面选型/前三章写作参考模板.md)、[选型与历史记录](20261014_电商数据与页面选型/先看这里.md) | 过程资料，不作为最终交付文件 |

Word中的实验参考资料[6]对应`02_实验研究/forecast_benchmark/`。报告正文和旧实验结论未因整理而修改；历史日志中的旧绝对路径保留，按[搬迁记录](03_测试与验证/文件整理/README.md)查找现址。
''')
write(DELIVERY/'README.md','''# 交付内容

- [前三章Word](基于FastAPI的智能电商运营管理系统_前三章参考稿.docx)：当前唯一交付文档，原文件直接迁入，正文与版式未改；个人封面信息待补齐。
- [系统源码与启动入口](../电商运营系统/README.md)：按固定路径约定保留原位。源码、页面、依赖和启动文件与运行缓存属于系统包；不混入报告渲染或实验结果。

预测实验见[实验包](../02_实验研究/forecast_benchmark/README.md)；旧Word、生成脚本、图源与PDF/PNG核查材料见[报告验证包](../03_测试与验证/word_qa/README.md)。

复现依赖由各包说明；交付目录无需存放中间版、测试日志或原始Excel副本。整理不代表已经提交或老师审核通过。
''')
write(EXPERIMENT/'README.md','''# 日销量预测实验

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
''')
write(QA/'README.md','''# 测试与验证

| 材料 | 入口 | 范围 |
|---|---|---|
| 报告生成与版式核验 | [word_qa](word_qa/README.md) | 生成脚本、旧Word、图源、PDF及逐页渲染、构建记录 |
| 系统测试与验收 | [系统测试](系统测试/README.md) | 测试脚本、样例、测试结果、验收JSON及截图 |
| 数据下载与基础核验 | [数据获取核验](数据获取核验/README.md) | 下载脚本、来源记录、日志与失败下载片段 |
| 本次文件整理 | [整理记录](文件整理/README.md) | 迁移映射、前后校验值、路径检查结果 |

这里的结果是已有运行记录。本次仅重新检查文件完整性和搬迁后的路径，未重跑模型、完整功能测试、联网下载或报告生成。
''')
write(WORD/'README.md','''# 报告生成与版式验证

交付文件：[前三章Word](../../01_交付内容/基于FastAPI的智能电商运营管理系统_前三章参考稿.docx)。本目录内的DOCX均为过程版本，不作为最新交付。

- [原构建与验收记录](artifact.md)、[构建清单](build_manifest.json)。
- 最新保存的版式证据：[revision_verified.pdf](revision_verified.pdf)、[逐页PNG](revision-verified-render/)。其余PDF和渲染目录保留为历史过程，不混入交付目录。
- 修订依赖：revision_before.docx、revision_content.json、revise_report.py、revision_figures.py、build_report.py中的绘图帮助函数；再经revise_render.ps1与finalize_revision.py处理。
- 原学校模板仍在原微信文件目录；复现依赖docx、Pillow、lxml、pypdf、原生Word兼容COM及Poppler，具体以artifact.md历史记录为准。

2026-10-22仅调整位置与脚本路径，没有执行上述流程。revise_report.py等续修入口指向交付目录；旧版build_report.py主入口改为本目录的“旧版重新生成_前三章参考稿.docx”，避免覆盖当前版本。历史任务文本保留原样。
''')
write(TEST/'README.md','''# 系统测试与验收材料

系统源码保留在[电商运营系统](../../电商运营系统/README.md)。

| 内容 | 入口 |
|---|---|
| 服务测试脚本 | [tests/test_services.py](tests/test_services.py) |
| 真实数据与本地接口验收脚本 | [tests/verify_real.py](tests/verify_real.py) |
| 已有测试日志 | [tests_result.log](tests_result.log) |
| 已有验收结果与日志 | [acceptance.json](acceptance.json)、[acceptance.log](acceptance.log) |
| 数据准备与缓存迁移日志 | [runtime_prepare.log](runtime_prepare.log)、[cache_migration.log](cache_migration.log) |
| 页面截图 | [screenshots](screenshots/) |
| 保留测试样例 | [test_tmp](test_tmp/) |
| 历史开发任务 | [forecast_task.txt](forecast_task.txt) |

复现依赖与系统requirements.txt一致；从本目录运行`python -m unittest discover -s tests -p test_services.py -v`。真实接口验收需另行启动本地8777服务后运行`python tests/verify_real.py`；该验收包含回测，不能为检查搬迁而顺手执行。

测试临时数据继续输出到本包test_tmp，验收JSON输出到本包acceptance.json。服务正常运行所需的retail.sqlite3、data_manifest.json和models保留在系统runtime内。本次没有重跑这些测试，旧日志结论仅代表原运行。
''')
write(DOWNLOAD/'README.md','''# 数据下载与基础核验

共享数据保留原位：[online_retail_II.xlsx](../../20261014_电商数据与页面选型/online_retail_II.xlsx)、[数据说明](../../20261014_电商数据与页面选型/数据说明.txt)。

- [下载与核验脚本](download_dataset.py)、[原下载记录](download_record.json)、[原运行日志](download_run.log)。
- online_retail_ii.zip.part是旧官方连接失败的下载片段，完整保留作过程材料，不是可使用的数据交付。
- 脚本依赖Python、openpyxl及网络；原始Excel仍写入共享原料目录，核验记录、压缩包和下载片段写入本包。

复现时从本目录运行`python download_dataset.py --mirror`；会访问网络或重新核验，不属于本次整理。旧记录中的绝对路径是当时位置，当前地址以上方链接为准。镜像来源与官方原文件逐字节核对状态均不改变。
''')

p=ROOT/'电商运营系统'/'README.md'
t=p.read_text(encoding='utf-8').replace('实现与运行状态：开发中。脚本、服务、页面、测试与运行记录均在本包；数据缓存和日志在runtime/，不散落原资料目录。', '实现与运行状态沿用原开发记录。本包保留服务源码、页面、启动入口、依赖及运行所需数据库与模型；测试脚本、测试样例、截图和已有验收日志已迁入[系统测试包](../03_测试与验证/系统测试/README.md)。')
write(p,t+'\n\n## 文件整理后的入口\n\n- [报告交付](../01_交付内容/README.md)\n- [方法比较实验](../02_实验研究/forecast_benchmark/README.md)\n- [测试与验收](../03_测试与验证/系统测试/README.md)\n\n正常启动仍使用本目录的启动看板.cmd或启动看板.ps1，源码和运行数据库路径未变。测试复现从系统测试包执行，本次没有重新运行测试。\n')

p=WORD/'artifact.md'
write(p,p.read_text(encoding='utf-8')+'\n\n## 2026-10-22文件整理\n\n本包现址为03_测试与验证/word_qa；交付Word迁至02级之外的01_交付内容，原始数据和写作资料仍在20261014_电商数据与页面选型。当前相对路径见[包入口](README.md)。上文旧路径和历史验收描述保留。未重新生成Word、导出PDF或重跑预测；路径调整由Codex完成，当前会话无可调用Claude Code工具。\n')

p=WORD/'build_manifest.json'
manifest=json.loads(p.read_text(encoding='utf-8'))
manifest['historical_output_path_before_relocation']=manifest.get('output')
manifest['output']=str(DELIVERY/NAME)
manifest['figures']=[str(WORD/'figures'/Path(x.replace('\\','/')).name) for x in manifest.get('figures',[])]
for k,v in [('final_pdf','revision_final.pdf'),('final_page_images','revision-render-final'),('verified_pdf','revision_verified.pdf'),('verified_page_images','revision-verified-render')]:
    manifest[k]=v
manifest['relocated_on']='2026-10-22'
write(p,json.dumps(manifest,ensure_ascii=False,indent=2))

write(AUDIT/'README.md','''# 2026-10-22文件整理记录

本次按交付、实验、测试验证分类移动文件，保留原始Excel、系统源码、运行数据库与模型位置；没有删除旧版本、下载片段或测试样例。

- [整理前文件校验](整理前文件校验.json)：原文件相对路径与SHA-256。
- [迁移及核查结果](整理结果.json)：迁移对应关系、允许修改的脚本路径/说明文件、文件完整性核查与静态路径检查。
- organize_school_files.py是本次专用整理脚本的留档副本，仅供审阅，不可再次执行以免与现目录冲突。

实现工具：Codex；本会话没有可调用Claude Code工具。本次只运行文件整理和静态核查，未执行实验、模型训练、系统验收、网络下载或Word重建。原测试日志与验收结果不作为本次重新通过测试的证明。
''')
shutil.copy2(Path(__file__),AUDIT/'organize_school_files.py')

def relocated(old):
    p=Path(old)
    for m in mapping:
        s=Path(m['from'])
        if p==s or s in p.parents:
            return (Path(m['to'])/p.relative_to(s)).as_posix()
    return old

changed=[]
unchanged=0
for old,h in before.items():
    dest=ROOT/relocated(old)
    if not dest.is_file():
        raise RuntimeError(f'Missing original file: {old}')
    now=digest(dest)
    if now!=h:
        if rel(dest) not in edits:
            raise RuntimeError(f'Unexpected modification: {dest}')
        changed.append({'from':old,'to':rel(dest),'before':h,'after':now})
    else:
        unchanged+=1

python_files=[EXPERIMENT/'run_benchmark.py',*WORD.glob('*.py'),* (TEST/'tests').glob('*.py'),DOWNLOAD/'download_dataset.py']
for p in python_files:
    ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))

checks={
 'delivery_word_unchanged':digest(DELIVERY/NAME)==before[rel(DATA/NAME)],
 'raw_workbook_unchanged':digest(DATA/'online_retail_II.xlsx')==before[rel(DATA/'online_retail_II.xlsx')],
 'runtime_database_unchanged':digest(ROOT/'电商运营系统'/'runtime'/'retail.sqlite3')==before['电商运营系统/runtime/retail.sqlite3'],
 'all_original_files_retained':True,
 'python_syntax_checked_without_execution':len(python_files),
 'experiment_input_exists':(ROOT/'20261014_电商数据与页面选型'/'online_retail_II.xlsx').is_file(),
 'report_revision_inputs_exist':all((WORD/n).is_file() for n in ['revision_before.docx','revision_content.json','revision_figures.py','build_report.py']),
 'test_system_import_path_exists':(ROOT/'电商运营系统'/'services'/'data.py').is_file(),
 'report_manifest_matches_delivery_hash':digest(DELIVERY/NAME)==manifest['final_docx_sha256'],
}
if not all(checks.values()):
    raise RuntimeError(checks)

# Check all new entry-point links; historical notes may retain historical paths.
bad=[]
for p in [ROOT/'先看这里.md',DELIVERY/'README.md',EXPERIMENT/'README.md',QA/'README.md',WORD/'README.md',TEST/'README.md',DOWNLOAD/'README.md',AUDIT/'README.md']:
    for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
        if '://' not in target and not (p.parent/target.split('#')[0]).exists():
            if target=='整理结果.json' and p.parent==AUDIT:
                continue
            bad.append({'file':rel(p),'target':target})
checks['new_entry_links_valid']=not bad
if bad: raise RuntimeError(bad)

result={'date':'2026-10-22','project_root':str(ROOT),'moves':mapping,'original_file_count':len(before),'unchanged_original_files':unchanged,'path_and_documentation_edits':changed,'all_edited_or_created_files':sorted(set(edits)),'checks':checks,'experiments_rerun':False,'functional_tests_rerun':False,'report_rebuilt':False,'historical_evidence_preserved':True,'implementation_tool':'Codex; no callable Claude Code tools in this session'}
(AUDIT/'整理结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'moves':len(mapping),'original_files':len(before),'unchanged_files':unchanged,'edited_existing_files':len(changed),'checks':checks},ensure_ascii=False,indent=2))
