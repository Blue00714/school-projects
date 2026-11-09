"""Preserve opaque template parts after native field refresh and record checks."""
import hashlib
import json
import zipfile
from pathlib import Path
from docx import Document
from pypdf import PdfReader

Q = Path(__file__).resolve().parent
OUT = Q.parents[1] / '01_交付内容' / '基于FastAPI的智能电商运营管理系统_前三章参考稿.docx'
SOURCE = Path('C:/Users/Administrator/Documents/xwechat_files/wxid_pwitm54bwsyn22_16f5/msg/file/2026-10/专业项目设计报告-模板.docx')
EXPECTED = 'a78a6ccd55d218cd0d7c14235dd0826afc40cf2b0afca146240f542792fe657d'
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == EXPECTED
with zipfile.ZipFile(Q/'revision_authored.docx') as a, zipfile.ZipFile(OUT) as b:
    # Keep the native-refreshed TOC and field caches; restore all other parts.
    assert set(a.namelist()) == set(b.namelist())
    assert a.read('word/_rels/document.xml.rels') == b.read('word/_rels/document.xml.rels')
    native_document = b.read('word/document.xml')
    temp = OUT.with_suffix('.finalizing.docx')
    with zipfile.ZipFile(temp, 'w') as z:
        for info in a.infolist():
            z.writestr(info, native_document if info.filename == 'word/document.xml' else a.read(info))
temp.replace(OUT)
with zipfile.ZipFile(Q/'revision_before.docx') as a, zipfile.ZipFile(OUT) as b:
    changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
    assert set(changed) == {'word/document.xml', 'word/media/image6.png'}, changed
    assert b.testzip() is None
with zipfile.ZipFile(SOURCE) as a, zipfile.ZipFile(OUT) as b:
    assert all(a.read(n) == b.read(n) for n in ['word/media/image1.jpeg', 'word/media/image2.jpeg'])
before, final = Document(Q/'revision_before.docx'), Document(OUT)
assert len(final.tables) == 7 and len(final.inline_shapes) == 6 and len(final.sections) == 1
for attr in ['page_width','page_height','top_margin','bottom_margin','left_margin','right_margin','header_distance','footer_distance']:
    assert getattr(before.sections[0], attr) == getattr(final.sections[0], attr)
text = '\n'.join(p.text for p in final.paragraphs) + '\n' + '\n'.join(c.text for t in final.tables for r in t.rows for c in r.cells)
assert not any(s in text for s in ['【写作提示】','此处插入','学生成绩管理系统','〔待选定〕','余工'])
assert all(s in text for s in ['22.8%','55个起点','49个具有完整7天','模拟库存','ReAct'])
assert all(n in text for n in ['图1-1','图3-1','图3-2','图3-3'])
manifest = json.loads((Q/'build_manifest.json').read_text(encoding='utf-8'))
manifest.update({
    'revision_date':'2026-10-20', 'paragraphs':len(final.paragraphs),
    'tables':len(final.tables), 'images':len(final.inline_shapes),
    'rendered_pages':len(PdfReader(Q/'revision_final.pdf').pages),
    'authoring':'Claude Code CLI via official MCP attempted helper implementation; tool writes denied. Codex recovered and reviewed helper implementation, authored revision text, corrected formatting and business flow, executed and verified locally. CLI route: deepseek-flash[1m].',
    'render_status':'Native Word-compatible COM field refresh and PDF export; bundled Poppler rasterization; all 15 pages visually checked. Packaged render_docx.py unavailable because LibreOffice is absent.',
    'final_pdf':'revision_final.pdf', 'final_page_images':'revision-render-final',
    'final_docx_sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),
    'source_sha256':EXPECTED, 'school_artwork_preserved_byte_for_byte':True,
    'opaque_parts_preserved':True, 'changed_parts_vs_previous_word':changed,
    'toc_and_page_fields':'Updated by native application; cached TOC retained; updateFields remains enabled',
    'limitations':'Cover personal fields blank. Frontend/database are proposed design choices, model API provider not fixed. Existing local model experiments are acknowledged; system integration, full data audit and real business benefits are not claimed complete.',
    'reproduction':'revision_before.docx + revision_content.json -> revise_report.py (imports revision_figures.py) -> revise_render.ps1 -> finalize_revision.py; then export read-only and rasterize for QA. Original school template and original Markdown preserved.',
})
(Q/'build_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'pages':manifest['rendered_pages'],'tables':7,'design_figures':4,'changed_parts':changed,'sha256':manifest['final_docx_sha256']},ensure_ascii=False))
