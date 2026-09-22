# -*- coding: utf-8 -*-
"""最小可跑检查：把最容易算错的地方钉死。

跑法：  python tests/test_fias.py     （不依赖 pytest）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kj import fias, students, store  # noqa: E402


def T(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        raise AssertionError(name)


def main():
    print("=== 1. 编码边界（最容易判错的几条）===")
    T('"大家记住，考试要考" → 第 6 类【给出指令】，不是第 5 类【讲授】',
      fias.rule_classify("teacher", "大家记住，考试要考") == 6)
    T('"很好！那谁能说说为什么？" → 第 4 类【提问】（按主导意图）',
      fias.rule_classify("teacher", "很好！那谁能说说为什么？") == 4)
    T('"很好，说得对" → 第 2 类【表扬鼓励】',
      fias.rule_classify("teacher", "很好，说得对") == 2)
    T('"按你说的来" → 第 3 类【采纳想法】',
      fias.rule_classify("teacher", "按你说的来") == 3)
    T('学生被点名后作答 → 第 8 类【应答】',
      fias.rule_classify("student", "等于负六", is_response=True) == 8)
    T('学生未被提问而追问 → 第 9 类【主动发起】',
      fias.rule_classify("student", "那如果是分数呢", is_response=False) == 9)

    print("\n=== 2. 指标公式与除零保护 ===")
    turns = [
        {"speaker": "teacher", "fias": 5, "text": "讲", "name": "师"},
        {"speaker": "teacher", "fias": 4, "text": "谁能说说为什么", "name": "师", "target": "小林"},
        {"speaker": "student", "fias": 8, "text": "答", "name": "小林", "wait_ms": 3200},
        {"speaker": "teacher", "fias": 2, "text": "很好", "name": "师"},
        {"speaker": "student", "fias": 9, "text": "追问", "name": "小周"},
    ]
    m = fias.compute_metrics(turns, ["小林", "小周"])
    T("IDR = (1..4类)/(5..7类) = (第4类+第2类)/第5类 = 2/1 = 2.0",
      m["IDR"] == 2.0, f"实际 {m['IDR']}")
    T("TT = 3/5 = 0.6", m["TT"] == 0.6, f"实际 {m['TT']}")
    T("ST = 2/5 = 0.4", m["ST"] == 0.4, f"实际 {m['ST']}")
    T("SIR = 第9类/(第8+9类) = 1/2 = 0.5", m["SIR"] == 0.5, f"实际 {m['SIR']}")
    T("平均等待时间 = 3.2 秒", m["avg_wait_s"] == 3.2, f"实际 {m['avg_wait_s']}")
    T("等待 3.2s ≥1.5s，不算短等待", m["short_wait_rate"] == 0.0, f"实际 {m['short_wait_rate']}")
    T("没有自问自答 → self_answer_rate = 0.0", m["self_answer_rate"] == 0.0,
      f"实际 {m['self_answer_rate']}")
    T("叫答分布按【实际作答者】统计（小林 = 1）", m["call_distribution"]["小林"] == 1,
      f"实际 {m['call_distribution']}")
    T("未点名的学生被识别为被忽略的学生", m["ignored_students"] == ["小周"],
      f"实际 {m['ignored_students']}")

    print("\n=== 2.5 「自问自答」必须与「提问后马上叫学生」区分开 ===")
    sa = [
        {"speaker": "teacher", "fias": 4, "text": "谁会做这道题？", "name": "师"},
        {"speaker": "teacher", "fias": 5, "text": "好那我自己讲一遍。", "name": "师",
         "wait_ms": 800},
    ]
    ms = fias.compute_metrics(sa, ["小林"])
    T("老师提了问又自己接着说 → self_answer_rate = 1.0", ms["self_answer_rate"] == 1.0,
      f"实际 {ms['self_answer_rate']}")
    T("这种情况不算学生获得回答机会", ms["answered_questions"] == 0,
      f"实际 {ms['answered_questions']}")

    print("\n=== 3. 除零保护（老师一句提问都没有时不能崩）===")
    only_lecture = [{"speaker": "teacher", "fias": 5, "text": "讲", "name": "师"}]
    m0 = fias.compute_metrics(only_lecture, ["小林"])
    T("无提问时 IDR 正常计算为 0.0", m0["IDR"] == 0.0, f"实际 {m0['IDR']}")
    T("无提问时 SIR 返回 None 而不是崩", m0["SIR"] is None, f"实际 {m0['SIR']}")
    T("无提问时平均等待时间为 None", m0["avg_wait_s"] is None)

    print("\n=== 4. 学生状态机：答错后不能自己变聪明 ===")
    cast = students.create_cast(Path(__file__).resolve().parent.parent
                               / "knowledge" / "misconceptions.yaml", 3, seed=7)
    s1 = cast[0]
    r = s1.reply("小林，你来说说 (-3)×(-2) 等于多少？", is_question=True, is_called=True)
    T("被提问且未被说服 → 一定答错", r["behavior"] == "答错并坚持", r["behavior"])
    T("第一次答错后，认知状态仍未被说服", s1.state["convinced"] is False)
    r2 = s1.reply("那你为什么这么想？", is_question=True, is_called=True)
    T("第二次追问，仍然坚持错误", r2["behavior"] == "答错并坚持", r2["behavior"])

    print("\n=== 5. 可复现：同一 seed 生成同一批学生 ===")
    a = students.create_cast(Path(__file__).resolve().parent.parent
                            / "knowledge" / "misconceptions.yaml", 3, seed=7)
    b = students.create_cast(Path(__file__).resolve().parent.parent
                            / "knowledge" / "misconceptions.yaml", 3, seed=7)
    T("两次生成的姓名/原型/迷思概念完全一致",
      [(x.name, x.archetype, x.misconception["id"]) for x in a]
      == [(x.name, x.archetype, x.misconception["id"]) for x in b])

    print("\n=== 6. 事件流可重算（评价可审计）===")
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "kj_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    st = store.Store(tmp)
    sid = st.new_session("测试课", 1)
    for i, t in enumerate(turns, 1):
        st.add_turn(sid, i, t["speaker"], t["name"], t["text"], t["fias"],
                    wait_ms=t.get("wait_ms"), target=t.get("target"))
    m_a = fias.compute_metrics(st.turns(sid), ["小林", "小周"])
    m_b = fias.compute_metrics(st.turns(sid), ["小林", "小周"])
    T("从事件流重算两次，结果完全一致", m_a == m_b)

    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
