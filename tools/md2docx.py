# -*- coding: utf-8 -*-
"""把 Markdown 说明书转成 .docx（交比赛用）

用途：比赛要求提交 Word，而且要在 Word 里核对"字数 ≤2500"。
本脚本只处理说明书用到的语法：标题 / 段落 / 表格 / 引用块 / 列表 / **加粗** / 代码块。

跑法：  python tools/md2docx.py <输入.md> <输出.docx>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor


def add_runs(par, text: str) -> None:
    """把 **加粗** 转成 Word 的粗体分段"""
    for i, seg in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if seg == "":
            continue
        run = par.add_run(seg)
        run.bold = (i % 2 == 1)


def is_table_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:\-|]+\|", line.strip()))


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def convert(md_path: Path, out_path: Path) -> tuple[int, int]:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)

    i, table_buf = 0, []
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # ---- 表格：把连续的 | 行收集起来 ----
        if line.startswith("|"):
            table_buf.append(line)
            i += 1
            continue
        if table_buf:
            rows = [r for r in table_buf if not is_table_sep(r)]
            table_buf = []
            if rows:
                ncol = max(len(split_row(r)) for r in rows)
                t = doc.add_table(rows=0, cols=ncol)
                t.style = "Table Grid"
                for ri, r in enumerate(rows):
                    cells = split_row(r)
                    cells += [""] * (ncol - len(cells))
                    tr = t.add_row()
                    for ci, val in enumerate(cells):
                        cell_par = tr.cells[ci].paragraphs[0]
                        add_runs(cell_par, val)
                        for run in cell_par.runs:
                            run.font.size = Pt(9)
                            if ri == 0:
                                run.bold = True
                doc.add_paragraph()
            continue

        # ---- 空行 ----
        if not line.strip():
            i += 1
            continue

        # ---- 分隔线 ----
        if re.fullmatch(r"-{3,}", line.strip()):
            i += 1
            continue

        # ---- 代码块 ----
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(12)
            run = p.add_run("\n".join(buf))
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = re.sub(r"[*`]", "", m.group(2))
            if level == 1:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = p.add_run(text)
                run.bold = True
                run.font.size = Pt(16)
            else:
                p = doc.add_paragraph()
                run = p.add_run(text)
                run.bold = True
                run.font.size = Pt(14 if level == 2 else 12)
                run.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
            i += 1
            continue

        # ---- 引用块 ----
        if line.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            add_runs(p, line.lstrip("> ").strip())
            for run in p.runs:
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            i += 1
            continue

        # ---- 列表 ----
        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            add_runs(p, m.group(2))
            i += 1
            continue
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            add_runs(p, f"{m.group(2)}. {m.group(3)}")
            i += 1
            continue

        # ---- 普通段落 ----
        p = doc.add_paragraph()
        add_runs(p, line)
        i += 1

    doc.core_properties.author = ""      # ★ 双盲：清空文档属性里的作者
    doc.core_properties.comments = ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))

    # ---- 统计"Word 口径"的字数：中文字符 + 英文/数字单词 ----
    text = md_path.read_text(encoding="utf-8")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9\.\-_:]*", text))
    return cjk, latin


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法：python tools/md2docx.py <输入.md> <输出.docx>")
        sys.exit(1)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    cjk, latin = convert(src, dst)
    print(f"已生成：{dst}")
    print(f"中文字符 {cjk}　英文/数字单词 {latin}　Word 口径合计约 {cjk + latin}")
    print("⚠️ 最终请务必在 Word 里用【审阅 → 字数统计】再核一次（上限 2500）")
