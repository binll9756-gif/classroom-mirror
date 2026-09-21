# -*- coding: utf-8 -*-
"""
FIAS 编码规则 + 指标计算 —— 【纯代码，不调用任何大模型】

★ 这个文件是整个项目最重要的一条设计的落地：
      LLM 只负责"这句话属于第几类"（分类）
      指标（TT/ST/IDR/SIR、等待时间、叫答分布）必须由代码计算
  原因：同一份对话重复计算必须得到完全一样的结果，否则评价就不可信、无法申诉。

★ 本文件里的 rule_classify 是【离线兜底分类器】（按关键词规则），
  作用有二：
    1) 没有 API Key 时也能把整条链路跑通（本文档的演示就是用它）；
    2) 作为 LLM 分类的对照基线 —— 如果 LLM 连关键词规则都打不过，说明提示词有问题。
  生产环境应把分类换成一次 LLM 调用（把 FIAS_CODES 和边界规则放进提示词），
  但【compute_metrics 不要改】。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 九类定义
FIAS_CODES = {
    1: "接受情感",
    2: "表扬鼓励",
    3: "采纳想法",
    4: "提问",
    5: "讲授",
    6: "给出指令",
    7: "批评",
    8: "学生应答",
    9: "学生主动发起",
}
INDIRECT = (1, 2, 3, 4)   # 教师"引导"
DIRECT = (5, 6, 7)        # 教师"灌输"
STUDENT = (8, 9)

# 判定用的关键词（顺序即优先级：6/7 → 4 → 2/1/3 → 5）
_KW_DIRECTIVE = ("记住", "考试", "翻到", "把书", "大家注意", "不许", "安静", "抄下来")
_KW_CRITICIZE = ("怎么又", "这么简单", "不对吧", "你又", "不认真", "错了没有")
_KW_QUESTION = ("吗", "呢", "为什么", "谁能", "怎么", "是什么", "多少", "试试", "想想", "？", "?")
_KW_PRAISE = ("很好", "很棒", "说得对", "不错", "非常好", "厉害", "对了", "真有")
_KW_FEELING = ("没关系", "别紧张", "我理解", "有点难", "别怕", "不要紧")
_KW_ADOPT = ("按你说的", "你说的", "刚才说", "就按你", "你的想法", "顺着你", "按你的")

_KW_BLOOM = {
    "记忆": ("记住", "是什么", "背", "定义", "叫什么"),
    "理解": ("为什么", "解释", "说明", "怎么理解", "意思是", "怎么想", "怎么", "同意"),
    "应用": ("如果", "算一下", "试算", "应用", "举个例子"),
}


def rule_classify(speaker: str, text: str, is_response: bool = False) -> int:
    """离线兜底分类器。speaker: 'teacher' | 'student'"""
    t = (text or "").strip()
    if speaker == "student":
        return 8 if is_response else 9
    if any(k in t for k in _KW_DIRECTIVE):
        return 6
    if any(k in t for k in _KW_CRITICIZE):
        return 7
    if any(k in t for k in _KW_QUESTION):
        return 4
    if any(k in t for k in _KW_PRAISE):
        return 2
    if any(k in t for k in _KW_FEELING):
        return 1
    if any(k in t for k in _KW_ADOPT):
        return 3
    return 5


def bloom_guess(text: str) -> str:
    """极粗的提问层次猜测（仅用于演示；生产建议也交给 LLM，但要固定标签集）"""
    t = text or ""
    for level, kws in _KW_BLOOM.items():
        if any(k in t for k in kws):
            return level
    return "未判定"


def compute_metrics(turns: list[dict], roster: list[str]) -> dict:
    """从事件流计算指标。turns: 有序的 turn 列表，每项含 speaker/fias/name/text/wait_ms。"""
    counts = {c: 0 for c in FIAS_CODES}
    for t in turns:
        c = t.get("fias")
        if c in counts:
            counts[c] += 1
    total = sum(counts.values())

    def ratio(num, den):
        return round(num / den, 3) if den else None

    tt = ratio(sum(counts[c] for c in INDIRECT + DIRECT), total)
    st = ratio(sum(counts[c] for c in STUDENT), total)
    idr = ratio(sum(counts[c] for c in INDIRECT), sum(counts[c] for c in DIRECT))
    sir = ratio(counts[9], counts[8] + counts[9])

    # 等待时间 / 抢答率：教师提问(4)之后，下一位发言者的等待时长
    waits, rush = [], 0
    q_count = 0
    answered = 0
    for i, t in enumerate(turns):
        if t.get("fias") != 4 or t.get("speaker") != "teacher":
            continue
        q_count += 1
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        if nxt is None:
            rush += 1
            continue
        w = nxt.get("wait_ms")
        if nxt.get("speaker") == "student":
            answered += 1
            if w is not None:
                waits.append(w)
                if w < 1500:
                    rush += 1
        else:
            rush += 1  # 自问自答 = 抢答

    # 叫答分布（只统计教师明确点名/提问指向的对象）
    call = {name: 0 for name in roster}
    for t in turns:
        if t.get("speaker") == "teacher" and t.get("target") in call:
            call[t["target"]] += 1
    ignored = [n for n, v in call.items() if v == 0]

    bloom = {}
    for t in turns:
        if t.get("fias") == 4:
            lv = bloom_guess(t.get("text", ""))
            bloom[lv] = bloom.get(lv, 0) + 1
    mem_q = bloom.get("记忆", 0)

    return {
        "counts": counts,
        "total": total,
        "TT": tt, "ST": st, "IDR": idr, "SIR": sir,
        "teacher_questions": q_count,
        "answered_questions": answered,
        "avg_wait_s": round(sum(waits) / len(waits) / 1000, 2) if waits else None,
        "rush_rate": ratio(rush, q_count),
        "call_distribution": call,
        "ignored_students": ignored,
        "bloom_distribution": bloom,
        "memory_question_ratio": ratio(mem_q, q_count),
    }


# 专家/新手人类教师基线（引自 NAACL 2025 论文 Table 2，仅作对照刻度）
BASELINE_IDR = {"专家人类教师": 1.473, "新手人类教师": 0.885, "AI 教师(多智能体课堂)": 0.058}


def format_metrics(m: dict, label: str = "") -> str:
    """把指标渲染成人类可读的一段（报告页就是照着这个做的）"""
    lines = []
    if label:
        lines.append(f"—— {label} ——")
    lines.append(f"  发言总数 {m['total']}　教师提问 {m['teacher_questions']} 次（其中 {m['answered_questions']} 次由学生作答）")
    lines.append(f"  TT 教师话语比 = {m['TT']}　　ST 学生话语比 = {m['ST']}")
    idr = m["IDR"]
    lines.append(f"  ★ IDR 间接/直接影响比 = {idr}")
    for k, v in BASELINE_IDR.items():
        lines.append(f"      （对照）{k}：{v}")
    lines.append(f"  SIR 学生主动发起比 = {m['SIR']}　← ⚠️ 受学生参与度参数影响，不作为师范生能力证据")
    lines.append(f"  平均等待时间 = {m['avg_wait_s']} 秒　抢答率 = {m['rush_rate']}　（教育学研究建议 ≥3 秒）")
    lines.append(f"  记忆型提问占比 = {m['memory_question_ratio']}　提问层次分布 = {m['bloom_distribution']}")
    lines.append(f"  叫答分布 = {m['call_distribution']}")
    if m["ignored_students"]:
        lines.append(f"  🔴 被忽略的学生（整节课 0 次被叫到）：{'、'.join(m['ignored_students'])}")
    return "\n".join(lines)
