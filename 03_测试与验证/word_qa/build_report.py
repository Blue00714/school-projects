"""Build the school report from its retained DOCX and reviewed text."""
from __future__ import annotations
import copy
import hashlib
import html
import json
import math
import re
import shutil
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parents[1] / "20261014_电商数据与页面选型"
SOURCE = Path('C:/Users/Administrator/Documents/xwechat_files/wxid_pwitm54bwsyn22_16f5/msg/file/2026-10/专业项目设计报告-模板.docx')
SOURCE_HASH = 'a78a6ccd55d218cd0d7c14235dd0826afc40cf2b0afca146240f542792fe657d'
TEXT = PACKAGE / '前三章写作参考模板.md'
OUTPUT = ROOT / '旧版重新生成_前三章参考稿.docx'  # Legacy builder must not overwrite current delivery.
FIGURES = ROOT / 'figures'
FONT = 'C:/Windows/Fonts/msyh.ttc'
SERIF = 'C:/Windows/Fonts/simsun.ttc'


class Figure:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.image = Image.new('RGB', (width, height), 'white')
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>']

    def text(self, xy, value, size=44, fill='#111111', center=True):
        font = ImageFont.truetype(FONT, size)
        lines = value.split('\n')
        line_height = round(size * 1.40)
        total = len(lines) * line_height
        for i, line in enumerate(lines):
            x, y = xy[0], xy[1] - total / 2 + i * line_height
            bbox = self.draw.textbbox((0, 0), line, font=font)
            if center:
                x -= (bbox[2] - bbox[0]) / 2
            self.draw.text((x, y - bbox[1] + (line_height - bbox[3] + bbox[1]) / 2), line, font=font, fill=fill)
            anchor = 'middle' if center else 'start'
            self.svg.append(f'<text x="{xy[0]}" y="{y + line_height * .78}" font-family="Microsoft YaHei,SimSun,sans-serif" font-size="{size}" text-anchor="{anchor}" fill="{fill}">{html.escape(line)}</text>')

    def box(self, box, value='', size=44, fill='#F3F4F5', ellipse=False):
        x1, y1, x2, y2 = box
        if ellipse:
            self.draw.ellipse(box, fill=fill, outline='#222222', width=3)
            self.svg.append(f'<ellipse cx="{(x1+x2)/2}" cy="{(y1+y2)/2}" rx="{(x2-x1)/2}" ry="{(y2-y1)/2}" fill="{fill}" stroke="#222" stroke-width="3"/>')
        else:
            self.draw.rectangle(box, fill=fill, outline='#222222', width=3)
            self.svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" fill="{fill}" stroke="#222" stroke-width="3"/>')
        if value:
            self.text(((x1+x2)/2, (y1+y2)/2), value, size)

    def line(self, points, arrow=False, label=None, label_xy=None, size=36):
        self.draw.line(points, fill='#222222', width=3, joint='curve')
        joined = ' '.join(f'{x},{y}' for x,y in points)
        self.svg.append(f'<polyline points="{joined}" fill="none" stroke="#222" stroke-width="3"/>')
        if arrow:
            x0,y0 = points[-2]; x1,y1 = points[-1]
            angle = math.atan2(y1-y0,x1-x0)
            wings = [(x1,y1), (x1-18*math.cos(angle-.42),y1-18*math.sin(angle-.42)), (x1-18*math.cos(angle+.42),y1-18*math.sin(angle+.42))]
            self.draw.polygon(wings, fill='#222222')
            self.svg.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in wings)+'" fill="#222"/>')
        if label:
            if label_xy is None:
                a,b=points[0],points[-1]; label_xy=((a[0]+b[0])/2,(a[1]+b[1])/2-28)
            # A small white backing keeps relationship labels separate from strokes.
            w=max(len(s) for s in label.split('\n'))*size+20
            h=len(label.split('\n'))*size*1.4+10
            x,y=label_xy
            self.draw.rectangle((x-w/2,y-h/2,x+w/2,y+h/2),fill='white')
            self.svg.append(f'<rect x="{x-w/2}" y="{y-h/2}" width="{w}" height="{h}" fill="white"/>')
            self.text(label_xy,label,size)

    def actor(self,x,y,name):
        self.draw.ellipse((x-32,y-115,x+32,y-51),outline='#222222',width=4)
        self.svg.append(f'<circle cx="{x}" cy="{y-83}" r="32" fill="none" stroke="#222" stroke-width="4"/>')
        for points in [[(x,y-51),(x,y+65)],[(x-62,y-12),(x+62,y-12)],[(x,y+65),(x-55,y+135)],[(x,y+65),(x+55,y+135)]]:
            self.line(points)
        self.text((x,y+188),name,44)

    def save(self,name):
        FIGURES.mkdir(exist_ok=True)
        self.image.save(FIGURES/(name+'.png'),dpi=(300,300))
        (FIGURES/(name+'.svg')).write_text('\n'.join(self.svg+['</svg>']),encoding='utf-8')


def make_figures():
    f=Figure(1900,1150)
    for box,title in [((90,20,1390,230),'页面层'),((90,300,1390,470),'接口层'),((90,540,1390,790),'业务服务层'),((90,900,1390,1110),'数据与模型层')]:
        f.box(box,fill='white'); f.text((140,box[1]+55),title,44,center=False)
    labels=['商品订单','运营看板','预测回测','模拟库存','AI助手']
    for i,s in enumerate(labels):
        x=130+i*247; f.box((x,110,x+220,205),s,36)
    f.text((740,410),'FastAPI：路由、参数校验、权限与任务状态',40)
    for i,s in enumerate(['数据检查','销售统计','预测评价','库存规则','AI工具']):
        x=130+i*247; f.box((x,650,x+220,745),s,36)
    for x,s in [(145,'业务数据库'),(555,'原始文件与记录'),(965,'预测模型与结果')]:
        f.box((x,1000,x+350,1090),s,36)
    for y1,y2 in [(230,300),(470,540),(790,900)]:
        f.line([(740,y1),(740,y2)],arrow=True)
    f.box((1480,620,1840,800),'外部大模型API\n理解与表达',38)
    f.line([(1390,700),(1480,700)],arrow=True)
    f.line([(1660,800),(1660,850),(1280,850),(1280,790)],arrow=True,label='返回调用或回答',label_xy=(1580,880),size=32)
    f.save('图1-1_系统总体框架图')

    f=Figure(1900,1530)
    f.box((430,30,1370,1480),fill='white')
    f.text((900,80),'智能电商运营管理系统',43)
    names=['登录与退出','账号维护','配置与任务状态','导入与检查数据','商品订单查询与看板','预测与历史回测','模拟库存与补货提示','AI数据查询','报告生成与导出']
    ys=[175+i*150 for i in range(len(names))]
    f.actor(180,345,'管理员');f.actor(1710,850,'运营人员')
    for y in ys[:3]:
        f.line([(245,335),(365,y),(590,y)])
    for i in [0,3,4,5,6,7,8]:
        f.line([(1645,840),(1470,ys[i]),(1210,ys[i])])
    for y,name in zip(ys,names):
        f.box((590,y-54,1210,y+54),name,40,fill='white',ellipse=True)
    f.save('图3-1_系统功能用例图')

    f=Figure(2100,1730)
    nodes={
        'user':(300,160,'用户\nuser_id'), 'batch':(1050,160,'导入批次\nbatch_id'), 'raw':(1800,160,'原始记录\nraw_id'),
        'ai':(300,610,'AI任务\nai_task_id'), 'order':(1050,610,'订单\norder_id'), 'item':(1800,610,'订单明细\nitem_id'),
        'report':(300,1060,'运营报告\nreport_id'), 'ptask':(1050,1060,'预测任务\npredict_id'), 'product':(1800,1060,'商品\nproduct_id'),
        'pres':(1050,1510,'预测结果\nresult_id'), 'ledger':(1800,1510,'模拟库存流水\nledger_id'),
    }
    # Relations are drawn first; nodes cover line endpoints cleanly.
    f.line([(550,160),(800,160)],label='1 : N',label_xy=(675,115))
    f.line([(1300,160),(1550,160)],label='1 : N',label_xy=(1425,115))
    f.line([(300,250),(300,520)],label='1 : N',label_xy=(300,385))
    f.line([(300,700),(300,970)],label='1 : 0..N',label_xy=(300,835))
    f.line([(1300,610),(1550,610)],label='1 : N',label_xy=(1425,565))
    f.line([(1800,970),(1800,700)],label='1 : N',label_xy=(1800,835))
    f.line([(1050,1150),(1050,1420)],label='1 : N',label_xy=(1050,1285))
    f.line([(1800,1150),(1800,1420)],label='1 : N',label_xy=(1800,1285))
    f.line([(1550,1060),(1420,1060),(1420,1510),(1300,1510)],label='1 : N',label_xy=(1420,1285))
    f.line([(1800,250),(1800,520)],label='来源映射',label_xy=(1800,385),size=36)
    for x,y,label in nodes.values():f.box((x-250,y-90,x+250,y+90),label,43,fill='white')
    f.text((1050,1670),'注：来源映射规则在重复与取消记录核查后确定；库存流水为模拟。',32)
    f.save('图3-2_系统概念ER图')

    f=Figure(1900,1750)
    boxes=[((650,60,1250,210),'导入历史交易数据'),((50,330,580,490),'显示问题清单\n修正或确认处理'),((650,650,1250,790),'确认处理与保存批次'),((650,900,1250,1040),'商品订单查询与看板'),((150,1220,750,1360),'销量预测与历史回测'),((1150,1220,1750,1360),'AI调用查询与预测工具'),((150,1510,750,1650),'模拟库存与补货提示'),((1150,1510,1750,1650),'核对并导出运营简报')]
    for b,t in boxes:f.box(b,t,42)
    diamond=[(950,280),(1200,410),(950,540),(700,410)]
    f.draw.polygon(diamond,fill='white',outline='#222222',width=3)
    f.svg.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in diamond)+'" fill="white" stroke="#222" stroke-width="3"/>')
    f.text((950,410),'数据检查\n是否可入库',42)
    f.line([(950,210),(950,280)],arrow=True)
    f.line([(700,410),(580,410)],arrow=True,label='否',label_xy=(635,365))
    f.line([(315,330),(315,135),(650,135)],arrow=True)
    f.line([(950,540),(950,650)],arrow=True,label='是',label_xy=(1000,595))
    f.line([(950,790),(950,900)],arrow=True)
    f.line([(950,1040),(950,1130),(450,1130),(450,1220)],arrow=True)
    f.line([(950,1130),(1450,1130),(1450,1220)],arrow=True)
    f.line([(450,1360),(450,1510)],arrow=True)
    f.line([(1450,1360),(1450,1510)],arrow=True)
    f.line([(750,1580),(1150,1580)],arrow=True,label='提供查询依据',label_xy=(950,1540),size=36)
    f.save('图3-3_系统主要业务流程图')


def set_run(run,name='宋体',size=12,bold=False):
    run.font.name=name;run.font.size=Pt(size);run.font.bold=bold
    run.font.color.rgb=RGBColor(0,0,0)
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),name)


def add_text(paragraph,text,size=12,bold=False):
    # Convert simple Markdown emphasis while keeping citation markers compact.
    for part in re.split(r'(\*\*.*?\*\*|\[[1-4]\])',text):
        if not part:continue
        is_bold=part.startswith('**') and part.endswith('**')
        part=part[2:-2] if is_bold else part
        r=paragraph.add_run(part);set_run(r,size=size,bold=bold or is_bold)
        if re.fullmatch(r'\[[1-4]\]',part):r.font.superscript=True


def paragraph(doc,text='',style=None):
    p=doc.add_paragraph(style=style)
    pf=p.paragraph_format;pf.first_line_indent=Pt(24);pf.line_spacing=Pt(20)
    pf.space_before=Pt(0);pf.space_after=Pt(2);pf.widow_control=True
    if text:add_text(p,text)
    return p


def heading(doc,text,level=1,page_break=False,front=False):
    p=doc.add_paragraph(style='Title' if front else f'Heading {level}')
    if front:
        outline=OxmlElement('w:outlineLvl');outline.set(qn('w:val'),'9');p._p.get_or_add_pPr().append(outline)
    p.paragraph_format.first_line_indent=Pt(0)
    p.paragraph_format.space_before=Pt(8 if level>1 else 0)
    p.paragraph_format.space_after=Pt(6)
    p.paragraph_format.line_spacing=Pt(22)
    p.paragraph_format.keep_with_next=True
    p.paragraph_format.page_break_before=page_break
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER if level==1 else WD_ALIGN_PARAGRAPH.LEFT
    set_run(p.add_run(text),name='黑体',size=16 if level==1 else 12,bold=True)
    return p


TABLE_TITLES={
    '用户角色':'表2-1 目标用户与主要操作',
    '技术或资源':'表2-2 技术路线与需验证事项',
    '风险':'表2-3 技术风险与处理方式',
    '编号':'表3-1 系统功能需求',
    '用例':'表3-2 重点功能操作需求',
    '参与者':'表3-3 用例图参与者与功能',
    '实体关系':'表3-4 核心实体关系说明',
}


def caption(doc,text):
    p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    pf=p.paragraph_format;pf.space_before=Pt(4);pf.space_after=Pt(8);pf.line_spacing=Pt(16);pf.first_line_indent=Pt(0)
    set_run(p.add_run(text),size=10.5)
    return p


def add_figure(doc,name,width=155):
    p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent=Pt(0)
    p.paragraph_format.keep_with_next=True
    p.paragraph_format.space_before=Pt(5);p.paragraph_format.space_after=Pt(0)
    p.add_run().add_picture(str(FIGURES/(name+'.png')),width=Mm(width))
    caption(doc,name.replace('_',' ',1))


def table(doc,rows):
    title=TABLE_TITLES.get(rows[0][0],'')
    if title:
        p=caption(doc,title);p.paragraph_format.keep_with_next=True;p.paragraph_format.space_after=Pt(4)
    t=doc.add_table(rows=1,cols=len(rows[0]));t.autofit=False
    width=doc.sections[0].page_width-doc.sections[0].left_margin-doc.sections[0].right_margin
    ratios={3:[.20,.34,.46],4:[.10,.19,.43,.28],5:[.14,.20,.27,.20,.19]}.get(len(rows[0]),[1/len(rows[0])]*len(rows[0]))
    for col,ratio in zip(t.columns,ratios):col.width=int(width*ratio)
    for index,values in enumerate(rows):
        cells=t.rows[0].cells if index==0 else t.add_row().cells
        for cell,ratio,value in zip(cells,ratios,values):
            cell.width=int(width*ratio)
            tcPr=cell._tc.get_or_add_tcPr()
            margins=OxmlElement('w:tcMar')
            for edge,val in [('top',60),('bottom',60),('left',85),('right',85)]:
                element=OxmlElement('w:'+edge);element.set(qn('w:w'),str(val));element.set(qn('w:type'),'dxa');margins.append(element)
            tcPr.append(margins)
            if index==0:
                shd=OxmlElement('w:shd');shd.set(qn('w:fill'),'EDEDED');tcPr.append(shd)
            p=cell.paragraphs[0];p.paragraph_format.first_line_indent=Pt(0);p.paragraph_format.space_after=Pt(0);p.paragraph_format.line_spacing=Pt(14)
            add_text(p,value,size=10.5,bold=index==0)
        trPr=t.rows[index]._tr.get_or_add_trPr();cant=OxmlElement('w:cantSplit');trPr.append(cant)
        if index==0:
            repeat=OxmlElement('w:tblHeader');trPr.append(repeat)
    borders=OxmlElement('w:tblBorders')
    for edge in ['top','left','bottom','right','insideH','insideV']:
        e=OxmlElement('w:'+edge);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'777777');borders.append(e)
    t._tbl.tblPr.append(borders)
    paragraph(doc,'').paragraph_format.space_after=Pt(0)


def source_evidence():
    digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if digest!=SOURCE_HASH:raise RuntimeError('Retained template hash changed')
    with zipfile.ZipFile(SOURCE) as z:
        inventory=[{'part':x.filename,'bytes':x.file_size,'sha256':hashlib.sha256(z.read(x.filename)).hexdigest()} for x in z.infolist()]
    (ROOT/'template_parts.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    d=Document(SOURCE)
    evidence=[]
    for i,p in enumerate(d.paragraphs):
        if p.text and (len(evidence)<18 or p.style.name.startswith('Heading')):
            evidence.append({'index':i,'text':p.text[:70],'style':p.style.name,'alignment':str(p.alignment),'line_spacing':str(p.paragraph_format.line_spacing),'runs':[{'font':r.font.name,'size_pt':r.font.size.pt if r.font.size else None,'bold':r.bold} for r in p.runs[:2]]})
    (ROOT/'template_style_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    return d


def build():
    source=source_evidence();make_figures()
    if OUTPUT.exists():OUTPUT.chmod(0o666)
    shutil.copy2(SOURCE,OUTPUT)
    OUTPUT.chmod(0o666)
    doc=Document(OUTPUT)
    section=doc.sections[0]
    geometry=(section.page_width,section.page_height,section.top_margin,section.right_margin,section.bottom_margin,section.left_margin)
    # Keep the template's first 17 cover paragraphs and its section properties.
    cover_elements={p._p for p in doc.paragraphs[:17]}
    for element in list(doc._element.body):
        if element.tag==qn('w:sectPr') or element in cover_elements:continue
        doc._element.body.remove(element)
    cover=doc.paragraphs
    if 'Title' not in doc.styles:
        doc.styles.add_style('Title',WD_STYLE_TYPE.PARAGRAPH)
    for style in doc.styles:
        if style.type==WD_STYLE_TYPE.PARAGRAPH and re.fullmatch(r'toc\s*[1-9]',style.name,re.I):
            style.paragraph_format.line_spacing=Pt(18)
            style.paragraph_format.space_before=Pt(0)
            style.paragraph_format.space_after=Pt(0)
            style.font.size=Pt(12)
    p=cover[3];p.style=doc.styles['Title'];p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent=Pt(0)
    for r in p.runs:set_run(r,name='黑体',size=22,bold=True)
    p=cover[5];p.clear();p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent=Pt(0);p.paragraph_format.left_indent=Pt(0);p.paragraph_format.line_spacing=Pt(25)
    # Remove source character-based first-line indentation from the long title slot.
    for ind in p._p.xpath('./w:pPr/w:ind'):
        for attr in ['firstLineChars','firstLine','leftChars','left']:
            ind.attrib.pop(qn('w:'+attr),None)
    for j,text in enumerate(['题目  基于FastAPI的智能电商运营','管理系统设计与实现']):
        if j:p.add_run().add_break()
        r=p.add_run(text);set_run(r,name='黑体',size=15,bold=True)
    for index,label in [(10,'学    号'),(11,'学生姓名'),(12,'指导教师')]:
        p=cover[index];p.clear();r=p.add_run(label+'    ');set_run(r,name='黑体',size=16,bold=True)
        r=p.add_run('                     ');set_run(r,name='黑体',size=16,bold=True);r.underline=True
    # Erase source title paragraph borders, keeping cover metadata underlining.
    for p in [cover[3],cover[5]]:
        for border in p._p.xpath('./w:pPr/w:pBdr'):border.getparent().remove(border)
    for style_name in ['Title','Heading 1','Heading 2','Heading 3']:
        style=doc.styles[style_name]
        if style.element.pPr is not None:
            for border in list(style.element.pPr.findall(qn('w:pBdr'))):style.element.pPr.remove(border)

    heading(doc,'目录',1,page_break=True,front=True)
    p=paragraph(doc);p.paragraph_format.first_line_indent=Pt(0)
    begin=OxmlElement('w:fldChar');begin.set(qn('w:fldCharType'),'begin')
    instr=OxmlElement('w:instrText');instr.set(qn('xml:space'),'preserve');instr.text=' TOC \\o "1-3" \\h \\z \\u '
    sep=OxmlElement('w:fldChar');sep.set(qn('w:fldCharType'),'separate')
    end=OxmlElement('w:fldChar');end.set(qn('w:fldCharType'),'end')
    for item in [begin,instr,sep]:p.add_run()._r.append(item)
    set_run(p.add_run('打开文档后更新目录'),size=12)
    p.add_run()._r.append(end)
    heading(doc,'摘  要',1,page_break=True,front=True)
    paragraph(doc,'针对电商运营中交易数据整理、销售统计、短期销量判断与报告生成相互分散的问题，本项目拟设计基于FastAPI的智能电商运营管理系统。系统以公开历史交易数据为验证基础，设置用户与配置、商品订单管理、运营分析、销量预测与回测、模拟库存及AI运营助手六个模块。后端通过统一接口连接数据处理、统计与预测功能，页面展示销售指标及对应明细。预测部分按时间划分训练、验证和测试，比较简单基准与机器学习方法，并通过历史时点回放检查误差。AI助手通过工具调用获取计算结果，对日期和商品等缺失参数进行追问，再生成有依据的解释与简报。由于原始数据缺少库存及采购成本，库存与补货功能采用明确标识的模拟场景。本阶段完成项目目标、可行性和功能需求的设计，为后续概要设计、系统实现与效果验证提供依据。')
    paragraph(doc,'关键词：FastAPI；电商运营；销量预测；历史回测；AI Agent')

    raw=TEXT.read_text(encoding='utf-8')
    body=raw[raw.index('# 1 项目概述'):raw.index('## 交稿前需要补齐的内容')]
    lines=body.splitlines();i=0;architecture_added=False;business_added=False
    while i<len(lines):
        line=lines[i].strip()
        if not line or line=='---':i+=1;continue
        if line.startswith('综合上述方向，本项目的重点'):
            i+=1;continue
        if line.startswith('建议约') or line.startswith('【') or line.startswith('前三章主要回答'):
            i+=1;continue
        if line.startswith('**〔此处插入图3-1'):
            paragraph(doc,'系统功能用例关系如图3-1所示，参与者通过相应功能完成运营操作。')
            add_figure(doc,'图3-1_系统功能用例图');i+=1;continue
        if line.startswith('**〔此处插入图3-2'):
            paragraph(doc,'系统主要实体及关系如图3-2所示。图中库存流水独立标为模拟，原始记录保留来源追溯。')
            add_figure(doc,'图3-2_系统概念ER图',155);i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                values=[x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\- ]+',x or '-') for x in values):rows.append(values)
                i+=1
            table(doc,rows);continue
        match=re.match(r'^(#{1,3})\s+(.+)',line)
        if match:
            if not architecture_added and match.group(2).startswith('2 项目可行性'):
                paragraph(doc,'拟采用的系统总体框架如图1-1所示。页面和AI助手共用统计与预测业务模块，大模型API负责理解和表达，业务数字由程序计算。')
                add_figure(doc,'图1-1_系统总体框架图');architecture_added=True
            h=match.group(2);level=len(match.group(1))
            heading(doc,h,level,page_break=level==1);i+=1;continue
        # Replace unresolved square brackets with honest design-stage statements.
        line=line.replace('〔待选定〕','（拟选型）').replace('〔待确定的测试场景与门槛〕','后续明确的测试场景与门槛')
        if re.match(r'^[1-7]\. ',line):
            concise_tasks=[
                '明确目标用户、系统角色、第一版范围及数据边界。',
                '建立公开交易数据导入、质量检查和商品订单查询流程。',
                '统一销售数量、销售额、订单数和排名口径，设计运营看板。',
                '设计未来7天预测和历史回测，比较简单基准与机器学习模型。',
                '设置模拟库存及补货条件，提供可核对的风险提示。',
                '设计AI工具调用与报告生成，保留结果依据和执行记录。',
                '完成架构、模块和数据库概要设计，准备后续实现与测试。',
            ]
            index=int(line[0])-1
            line=f'{index+1}. '+concise_tasks[index]
        if line.startswith('项目以已有电脑、公开数据'):
            line='项目使用已有电脑、公开数据和开源工具，费用按实际API及部署用量估算，通过缓存和调用上限控制。按需求、设计、实现、测试推进，第一版限定六模块，注明公开数据与模拟场景，保存处理规则和模型版本。'
        if line.startswith('时间安排上，先完成需求'):
            i+=1;continue
        if line.startswith('**性能与环境需求。**'):
            line='**性能与环境需求。** 第一版支持本机运行与单店演示，按明确的电脑配置、样本规模和并发条件测试导入、查询、预测及模型调用耗时。验收门槛与测试场景在实现前确定，性能结论以实际测试记录为依据。'
        if line.startswith('ER图主要体现'):
            line='ER图主要体现实体、标识和关系，具体字段类型及约束将在第四章展开。订单标识及来源映射根据实际数据质量检查确定。'
        line=re.sub(r'正式稿需要补充.*','',line)
        if line:
            p=paragraph(doc,line)
            if re.match(r'^\d+\. ',line):p.paragraph_format.first_line_indent=Pt(0);p.paragraph_format.left_indent=Pt(12)
        i+=1
    heading(doc,'3.3.3 主要业务流程',3)
    paragraph(doc,'系统主业务流程如图3-3所示。导入检查失败时反馈问题，确认处理后保存批次；查询、预测和模拟库存结果共同支持运营简报。')
    add_figure(doc,'图3-3_系统主要业务流程图')
    heading(doc,'参考资料',1,page_break=True,front=True)
    refs=[
        '[1] Chen D. Online Retail II[DS/OL]. UCI Machine Learning Repository, 2012. https://doi.org/10.24432/C5CG6D. 访问日期：2026-10-19。',
        '[2] FastAPI. FastAPI Documentation[EB/OL]. https://fastapi.tiangolo.com/. 访问日期：2026-10-19。',
        '[3] Hyndman R J, Athanasopoulos G. Forecasting: Principles and Practice[M/OL]. 3rd ed. https://otexts.com/fpp3/. 访问日期：2026-10-19。',
        '[4] macrozheng. mall-admin-web[CP/OL]. https://github.com/macrozheng/mall-admin-web. 访问日期：2026-10-19。',
    ]
    for text in refs:
        p=paragraph(doc);p.add_run(text);p.alignment=WD_ALIGN_PARAGRAPH.LEFT;p.paragraph_format.first_line_indent=Pt(-20);p.paragraph_format.left_indent=Pt(20)
        for r in p.runs:set_run(r,size=10.5)
    # Add reference markers to the reviewed discussion instead of invented papers.
    for p in doc.paragraphs:
        if '将mall-admin-web等开源项目' in p.text:add_text(p,'[4]')
        if '评价时需要保持时间顺序' in p.text:add_text(p,'[3]')
        if p.text.startswith('后端拟使用FastAPI提供接口'):add_text(p,'[2]')
    settings=doc.settings.element
    update=settings.find(qn('w:updateFields'))
    if update is None:update=OxmlElement('w:updateFields');settings.append(update)
    update.set(qn('w:val'),'true')
    doc.core_properties.author=''
    doc.core_properties.last_modified_by=''
    doc.core_properties.title='基于FastAPI的智能电商运营管理系统设计与实现'
    doc.core_properties.subject='前三章设计阶段参考稿'
    doc.save(OUTPUT)
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_HASH:raise RuntimeError('Source modified')
    final=Document(OUTPUT)
    actual=final.sections[0]
    if len(final.sections)!=1 or geometry!=(actual.page_width,actual.page_height,actual.top_margin,actual.right_margin,actual.bottom_margin,actual.left_margin):raise RuntimeError('Page geometry changed')
    joined='\n'.join(p.text for p in final.paragraphs)
    if '学生成绩管理系统' in joined or '【写作提示】' in joined or '此处插入' in joined:raise RuntimeError('Unreplaced source or writing instruction')
    with zipfile.ZipFile(SOURCE) as a,zipfile.ZipFile(OUTPUT) as b:
        for name in ['word/media/image1.jpeg','word/media/image2.jpeg']:
            if a.read(name)!=b.read(name):raise RuntimeError('School branding changed')
    manifest={'output':str(OUTPUT),'source_sha256':SOURCE_HASH,'paragraphs':len(final.paragraphs),'tables':len(final.tables),'images':len(final.inline_shapes),'figures':[str(p) for p in sorted(FIGURES.glob('*.png'))],'authoring':'Codex fallback; Claude Code CLI attempted but writes and execution were permission-denied; routed model deepseek-flash[1m]','render_status':'pending native Word-compatible COM PDF export and visual review'}
    (ROOT/'build_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


def restore_branding():
    """Restore the retained school JPEGs after the native editor's recompression."""
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_HASH:
        raise RuntimeError('Retained template changed')
    patched=ROOT/'branding-restored.docx'
    names=['word/media/image1.jpeg','word/media/image2.jpeg']
    with zipfile.ZipFile(SOURCE) as source,zipfile.ZipFile(OUTPUT) as final,zipfile.ZipFile(patched,'w') as out:
        for item in final.infolist():
            out.writestr(item,source.read(item.filename) if item.filename in names else final.read(item.filename))
    patched.replace(OUTPUT)
    with zipfile.ZipFile(SOURCE) as source,zipfile.ZipFile(OUTPUT) as final:
        assert final.testzip() is None
        assert all(source.read(name)==final.read(name) for name in names)
    print('Retained school artwork restored byte-for-byte; document XML and layout untouched')


if __name__=='__main__':
    if '--restore-branding' in sys.argv:restore_branding()
    else:build()
