# -*- coding: utf-8 -*-
"""教案解析（F1）

把一份教案（PDF / Word / 纯文本）解析成结构化 JSON，并**用代码**找出教学设计本身的漏洞。

★ 设计原则（和全项目一致）：
    · 需要「读懂语言」的部分（抽取目标、重难点、迷思概念）→ 交给模型
    · 需要「绝对可靠」的部分（目标—活动—评价是否对齐）→ 由代码判定
      因为对齐检查就是个集合运算，用模型做反而不可复现、还可能编。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import llm

# ------------------------------------------------------------------ 读取文件
def load_text(path: str | Path) -> str:
    """支持 .pdf / .docx / .txt / .md"""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".pdf":
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    if suf == ".docx":
        import docx
        d = docx.Document(str(p))
        parts = [x.text for x in d.paragraphs]
        for t in d.tables:
            for r in t.rows:
                parts.append(" | ".join(c.text for c in r.cells))
        return "\n".join(parts)
    return p.read_text(encoding="utf-8", errors="ignore")


# ------------------------------------------------------------------ 代码判定：对齐检查
def check_alignment(lesson: dict) -> list[str]:
    """★ 纯代码。找出「目标—活动—评价」不对齐的地方。这是本项目的杀手锏之一。

    返回人类可读的问题清单（每条都能落到具体的目标/活动上）。
    """
    gaps: list[str] = []
    objs = {o.get("id"): o for o in lesson.get("objectives", []) if o.get("id")}
    acts = lesson.get("activities", []) or []
    asss = lesson.get("assessments", []) or []

    for oid, o in objs.items():
        in_act = any(oid in (a.get("objective_refs") or []) for a in acts)
        in_ass = any(oid in (a.get("objective_refs") or []) for a in asss)
        name = (o.get("text") or "")[:22]
        if not in_act:
            gaps.append(f"目标 {oid}（{name}…）在教案里没有任何教学活动与之对应 → 目标落空风险")
        if not in_ass:
            gaps.append(f"目标 {oid}（{name}…）没有任何评价手段与之对应 → 不可评目标")

    for a in acts:
        if not (a.get("objective_refs") or []):
            gaps.append(f"活动「{a.get('name', '未命名')}」没有对应任何教学目标 → 游离活动")

    return gaps


# ------------------------------------------------------------------ 模型抽取
_SYS = """你是资深教学设计分析专家，熟悉《义务教育课程方案和课程标准》。
你的任务是读懂一份教案，抽取出结构化信息，并指出它可能的教学设计问题。
判断要克制：没有依据的不要编。"""

_USER_TMPL = """请分析下面这份{subject}（{grade}）教案，输出一个 JSON。

学科：{subject}　学段：{grade}

【输出结构（严格照此，字段名不要改）】
{{
  "subject": "学科",
  "grade": "学段",
  "topic": "课时主题",
  "objectives": [
    {{"id": "O1", "text": "教学目标（可测量的表述）", "level": "记忆/理解/应用/分析/评价/创造"}}
  ],
  "key_points": ["重点"],
  "difficult_points": ["难点"],
  "prior_knowledge": ["学生已具备的基础（学情假设）"],
  "likely_misconceptions": [
    {{"id": "M1", "name": "迷思概念简称", "student_says": ["学生会说的原话（像学生说的，别像老师）"]}}
  ],
  "activities": [
    {{"id": "A1", "name": "活动名", "objective_refs": ["O1"], "minutes": 5}}
  ],
  "assessments": [
    {{"id": "E1", "type": "随堂提问/练习/板演", "objective_refs": ["O1"], "bloom": "记忆/理解/应用"}}
  ],
  "design_gaps": ["你认为这份教案本身的问题（一句话一条）"]
}}

【硬要求】
1. `activities[].objective_refs` 与 `assessments[].objective_refs` 里只能出现 objectives 里真实存在的 id。
2. likely_misconceptions 给 3~5 条，必须是这门课学生**真实会犯**的典型错误。
3. student_says 要像 13 岁学生说的话，短、口语化。
4. design_gaps 可以留空数组，不要为了凑数硬编。

【教案原文】
{content}
"""


def analyze(text: str, subject: str = "初中数学", grade: str = "七年级下") -> dict | None:
    """把教案文本解析成结构化 JSON。失败返回 None。"""
    content = (text or "").strip()
    if not content:
        return None
    lesson = llm.chat_json(_SYS, _USER_TMPL.format(subject=subject, grade=grade,
                                                   content=content[:12000]),
                           temperature=0.2, max_tokens=2000)
    if not isinstance(lesson, dict):
        return None

    # ---- 规范化：保证下游拿到的字段一定存在 ----
    lesson.setdefault("subject", subject)
    lesson.setdefault("grade", grade)
    for key in ("objectives", "key_points", "difficult_points",
                "prior_knowledge", "likely_misconceptions",
                "activities", "assessments", "design_gaps"):
        lesson.setdefault(key, [])
    # id 补齐，避免下游 KeyError
    for i, o in enumerate(lesson["objectives"], 1):
        o.setdefault("id", f"O{i}")
    for i, m in enumerate(lesson["likely_misconceptions"], 1):
        m.setdefault("id", f"M{i}")

    # ---- ★ 用代码做对齐检查，并把结果并入 design_gaps（去重）----
    gaps = check_alignment(lesson)
    existing = set(lesson["design_gaps"])
    lesson["design_gaps"] = list(lesson["design_gaps"]) + [g for g in gaps if g not in existing]
    return lesson


# ------------------------------------------------------------------ 离线示例
DEMO_LESSON: dict = {
    "subject": "初中数学", "grade": "七年级下", "topic": "有理数乘法 —— 负负得正",
    "objectives": [
        {"id": "O1", "text": "能用自己的话说出「负负得正」的道理（不是背口诀）", "level": "理解"},
        {"id": "O2", "text": "能用公式正确计算两个负数相乘", "level": "应用"},
    ],
    "key_points": ["负数乘法的符号法则"],
    "difficult_points": ["从规律与数轴方向理解算理，而不是记忆结论"],
    "prior_knowledge": ["已掌握有理数加法；知道数轴的左右方向"],
    "likely_misconceptions": [
        {"id": "M1", "name": "负号消失论",
         "student_says": ["负号乘负号不就自动消失了吗？", "两个负号抵消掉，就成正的了。"]},
        {"id": "M2", "name": "只是规定论",
         "student_says": ["书上就是这么规定的，记住就行了嘛。", "背下来不就好了？"]},
        {"id": "M3", "name": "看不出规律",
         "student_says": ["(-3)×(-2) 为什么不是 -6？我算出来是负六。"]},
    ],
    "activities": [
        {"id": "A1", "name": "情境导入", "objective_refs": ["O1"], "minutes": 5},
        {"id": "A2", "name": "观察规律推导", "objective_refs": ["O1"], "minutes": 12},
        {"id": "A3", "name": "例题演练", "objective_refs": ["O2"], "minutes": 10},
    ],
    "assessments": [
        {"id": "E1", "type": "随堂提问", "objective_refs": ["O1"], "bloom": "理解"},
    ],
    "design_gaps": [],
}


def demo_lesson() -> dict:
    """离线兜底：一份内置的「负负得正」教案解析结果（跑演示不必上传文件）"""
    import copy
    les = copy.deepcopy(DEMO_LESSON)
    les["design_gaps"] = check_alignment(les)
    return les


if __name__ == "__main__":
    print(json.dumps(demo_lesson(), ensure_ascii=False, indent=2))
