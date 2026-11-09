#!/usr/bin/env python3
"""Deterministically patch revision_before.docx from revision_content.json."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
BASE = HERE / "revision_before.docx"
CONTENT = HERE / "revision_content.json"
OUT = HERE.parents[1] / "01_交付内容" / "基于FastAPI的智能电商运营管理系统_前三章参考稿.docx"
MANIFEST = HERE / "revision_patch_manifest.json"
DOC = "word/document.xml"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
AFTER_TEMPLATE = 82
REFERENCE_TEMPLATE = 131


def q(name):
    return W + name


def kids(el, name):
    return [child for child in el if child.tag == q(name)]


def first_text_run(el):
    for run in el.iter(q("r")):
        if run.find(q("t")) is not None:
            return run
    return None


def capture_style(par):
    ppr = par.find(q("pPr")) if par is not None else None
    run = first_text_run(par) if par is not None else None
    rpr = run.find(q("rPr")) if run is not None else None
    return (copy.deepcopy(ppr) if ppr is not None else None,
            copy.deepcopy(rpr) if rpr is not None else None)


def make_par(ppr, rpr, text):
    par = etree.Element(q("p"))
    if ppr is not None:
        par.append(copy.deepcopy(ppr))
    run = etree.SubElement(par, q("r"))
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    node = etree.SubElement(run, q("t"))
    node.text = text
    node.set(XML_SPACE, "preserve")
    return par


def has_drawing(el):
    return any(el.find(".//" + q(name)) is not None
               for name in ("drawing", "pict", "object"))


def replace_par(par, text, index):
    if has_drawing(par):
        raise ValueError(f"paragraph {index} contains a drawing; refusing to patch")
    _, rpr = capture_style(par)
    # The old requirement paragraphs start with a bold label; do not spread
    # that label's bold formatting across the replacement body paragraph.
    if index in range(102, 107) and rpr is not None:
        for name in ('b', 'bCs'):
            for element in list(rpr.findall(q(name))):
                rpr.remove(element)
    for child in list(par):
        if child.tag != q("pPr"):
            par.remove(child)
    run = etree.SubElement(par, q("r"))
    if rpr is not None:
        run.append(rpr)
    node = etree.SubElement(run, q("t"))
    node.text = text
    node.set(XML_SPACE, "preserve")


def replace_cell(cell, text, where):
    if has_drawing(cell):
        raise ValueError(f"{where} contains a drawing; refusing to patch cell")
    first_p = cell.find(q("p"))
    ppr = first_p.find(q("pPr")) if first_p is not None else None
    run = first_text_run(cell)
    rpr = run.find(q("rPr")) if run is not None else None
    for child in list(cell):
        if child.tag != q("tcPr"):
            cell.remove(child)
    cell.append(make_par(ppr, rpr, text))


def apply_table(table, matrix, table_index):
    rows = kids(table, "tr")
    if not rows:
        raise ValueError(f"table {table_index} has no rows")
    grid = table.find(q("tblGrid"))
    grid_cols = kids(grid, "gridCol") if grid is not None else []
    ncols = len(grid_cols) if grid_cols else len(kids(rows[0], "tc"))
    if ncols == 0:
        raise ValueError(f"table {table_index} has no columns")
    if len(matrix) < len(rows):
        raise ValueError(f"table {table_index}: matrix has {len(matrix)} rows, "
                         f"fewer than the existing {len(rows)}")
    for row_index, texts in enumerate(matrix):
        if len(texts) != ncols:
            raise ValueError(f"table {table_index} row {row_index}: "
                             f"{len(texts)} values for {ncols} columns")

    base_row = copy.deepcopy(rows[-1])
    anchor = rows[-1]
    for row_index, texts in enumerate(matrix):
        if row_index < len(rows):
            row = rows[row_index]
        else:
            row = copy.deepcopy(base_row)
            trpr = row.find(q("trPr"))
            if trpr is not None:
                header = trpr.find(q("tblHeader"))
                if header is not None:
                    trpr.remove(header)
                if len(trpr) == 0:
                    row.remove(trpr)
            anchor.addnext(row)
            anchor = row
        cells = kids(row, "tc")
        if len(cells) != ncols:
            raise ValueError(f"table {table_index} row {row_index} has "
                             f"{len(cells)} cells for {ncols} columns")
        for col_index, (cell, text) in enumerate(zip(cells, texts)):
            replace_cell(cell, text, f"table {table_index} row {row_index} "
                                    f"cell {col_index}")


def index_of(key, limit, label):
    try:
        index = int(key)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {label} index: {key!r}") from None
    if str(index) != str(key) and str(key).lstrip("+-0") != str(index):
        raise ValueError(f"invalid {label} index: {key!r}")
    if not 0 <= index < limit:
        raise ValueError(f"{label} index {index} outside 0..{limit - 1}")
    return index


def main():
    with zipfile.ZipFile(BASE, "r") as src:
        infos = src.infolist()
        if sum(info.filename == DOC for info in infos) != 1:
            raise ValueError(f"{BASE.name} must contain exactly one {DOC}")
        root = etree.fromstring(src.read(DOC))
        body = root.find(q("body"))
        if body is None:
            raise ValueError("document.xml has no w:body")
        paras = kids(body, "p")
        tables = kids(body, "tbl")
        content = json.loads(CONTENT.read_text(encoding="utf-8"))
        para_plan = content.get("paragraphs", {})
        after_plan = content.get("after", {})
        table_plan = content.get("tables", {})
        references = content.get("references", [])
        if not all(isinstance(plan, dict)
                   for plan in (para_plan, after_plan, table_plan)):
            raise ValueError("paragraphs, after and tables must be JSON objects")
        if not isinstance(references, list) or not all(
                isinstance(item, str) for item in references):
            raise ValueError("references must be a list of strings")
        if not 0 <= AFTER_TEMPLATE < len(paras):
            raise ValueError(f"after template paragraph {AFTER_TEMPLATE} missing")
        if not 0 <= REFERENCE_TEMPLATE < len(paras):
            raise ValueError(f"reference template paragraph {REFERENCE_TEMPLATE} missing")
        after_ppr, after_rpr = capture_style(paras[AFTER_TEMPLATE])
        ref_ppr, ref_rpr = capture_style(paras[REFERENCE_TEMPLATE])

        seen = set()
        for key in sorted(para_plan, key=int):
            index = index_of(key, len(paras), "paragraph")
            if index in seen:
                raise ValueError(f"duplicate paragraph index {index}")
            seen.add(index)
            text = para_plan[key]
            if not isinstance(text, str):
                raise ValueError(f"paragraph {index} text must be a string")
            replace_par(paras[index], text, index)

        for key in sorted(after_plan, key=int):
            index = index_of(key, len(paras), "after")
            values = after_plan[key]
            if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values):
                raise ValueError(f"after[{index}] must be a list of strings")
            anchor = paras[index]
            for value in values:
                anchor.addnext(make_par(after_ppr, after_rpr, value))
                anchor = anchor.getnext()

        seen_tables = set()
        for key in sorted(table_plan, key=int):
            index = index_of(key, len(tables), "table")
            if index in seen_tables:
                raise ValueError(f"duplicate table index {index}")
            seen_tables.add(index)
            matrix = table_plan[key]
            if not isinstance(matrix, list) or not matrix or not all(
                    isinstance(row, list) and all(isinstance(v, str) for v in row)
                    for row in matrix):
                raise ValueError(f"table {index} must be a non-empty matrix of strings")
            apply_table(tables[index], matrix, index)

        if references:
            sect = body.find(q("sectPr"))
            for text in references:
                par = make_par(ref_ppr, ref_rpr, text)
                if sect is not None:
                    sect.addprevious(par)
                else:
                    body.append(par)

        xml = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               + etree.tostring(root, encoding="UTF-8", xml_declaration=False))
        tmp = OUT.with_name(OUT.name + ".tmp")
        from revision_figures import build_flow
        build_flow()
        flow_image = (HERE / 'figures' / '图3-3_系统主要业务流程图.png').read_bytes()
        with zipfile.ZipFile(tmp, "w") as dst:
            for info in infos:
                data = xml if info.filename == DOC else src.read(info)
                if info.filename == 'word/media/image6.png':
                    data = flow_image
                dst.writestr(info, data)
        os.replace(tmp, OUT)

    manifest = {
        "input": BASE.name,
        "output": OUT.name,
        "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "input_sha256": hashlib.sha256(BASE.read_bytes()).hexdigest(),
        "paragraphs_patched": len(seen),
        "paragraph_indices": sorted(seen),
        "tables_patched": len(seen_tables),
        "table_indices": sorted(seen_tables),
        "references_inserted": len(references),
        "zip_members_total": len(infos),
        "zip_members_unchanged": len(infos) - 2,
        "zip_members_changed": 2,
        "flow_diagram_revision": "Product scenario inputs separated from whole-store forecasts",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
