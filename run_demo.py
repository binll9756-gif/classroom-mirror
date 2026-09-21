# -*- coding: utf-8 -*-
"""两轮试讲演示：不需要 API Key 也能完整跑通整条链路。

跑法：  python run_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kj import fias, llm, store, students  # noqa: E402

YAML = Path(__file__).parent / "knowledge" / "misconceptions.yaml"

# ============================================================ 两位老师的台词
# 第一轮：典型的"新手老师"——以讲授和指令为主，只叫一个学生，问完立刻自己答
ROUND1 = [
    ("今天我们讲负负得正，大家把课本翻到第 30 页。", None),
    ("负负得正的意思是，两个负数相乘，结果就是正数。", None),
    ("记住，看见两个负号就变成正号，这是规定。", None),
    ("我再讲一遍，负负得正就是两个负号相遇变成正号。", None),
    ("小林，负负得正的结论是什么？", "小林"),
    ("不对，这么简单都记不住，我说的是两个负号。", None),
    ("大家把这个结论抄下来，考试要考。", None),
    ("下面做练习册第一题。", None),
]
ROUND1_WAIT = 900      # 问完 0.9 秒就自己接话 → 抢答

# 第二轮：改进后——有提问、有等待、有采纳想法、有表扬、三个学生都叫到
ROUND2 = [
    ("上节课我们学了有理数加法，今天来看乘法。", None),
    ("先看这道题：(-3)×(-2) 等于多少？谁想说说自己是怎么想的？", "阿豪"),
    ("阿豪刚才说等于正数，我们就顺着他的思路看一看。", None),
    ("小林，你算出来的答案是什么？", "小林"),
    ("没关系，第一次容易混，我们慢慢来。", None),
    ("阿豪能主动说想法，这点很好。", None),
    ("我们来看这串式子的变化：3×(-2)=-6，2×(-2)=-4，1×(-2)=-2，0×(-2)=0，每次乘数减一，结果就加二。", None),
    ("那 (-1)×(-2) 应该等于多少？你是怎么想的？", "小林"),
    ("小林刚才说'负号消失'，我们按她的说法推一下，看看对不对。", None),
    ("小周，你同意小林的说法吗？", "小周"),
    ("所以乘 -1 相当于改变方向，负负得正就是这么来的。", None),
    ("注意，这里的关键是理解方向，而不是背口诀。", None),
    ("把这条规律记在笔记上，我们做两道练习。", None),
    ("现在请大家完成课本第 31 页的第 1、2 题。", None),
]
ROUND2_WAIT = 3500     # 提问后等 3.5 秒 —— 教育学研究建议 ≥3 秒


def run_round(store_: store.Store, cast: list[students.Student], script: list,
              wait_ms: int, round_no: int) -> int:
    """跑一轮试讲，返回 session_id"""
    sid = store_.new_session(lesson="初中数学·负负得正", round_no=round_no,
                            note=f"第{round_no}轮（{llm.status()}）")
    by_name = {s.name: s for s in cast}
    idx = 0
    last_was_question = False

    for text, target in script:
        is_q = any(k in text for k in ("吗", "呢", "为什么", "怎么", "多少", "谁", "？", "?"))
        f = fias.rule_classify("teacher", text)
        idx += 1
        store_.add_turn(sid, idx, "teacher", "师范生", text, f,
                        wait_ms=None, target=target, behavior="")
        print(f"  [T] {text}")

        # 若点名/提问了某个学生，就让他回应
        stu = by_name.get(target) if target else None
        if is_q and stu is not None:
            out = stu.reply(text, is_question=True, is_called=True)
            if out["text"]:
                idx += 1
                store_.add_turn(sid, idx, "student", stu.name, out["text"], 8,
                                wait_ms=wait_ms, behavior=out["behavior"])
                print(f"       [{stu.name}] {out['text']}   （{out['behavior']}，等待 {wait_ms/1000:.1f}s）")
        # 未点名的其他学生：可能会走神/抢答（由代码按参与度决定，保证可复现）
        for s in cast:
            if s is stu:
                continue
            out = s.reply(text, is_question=is_q, is_called=False)
            if out["text"]:
                idx += 1
                store_.add_turn(sid, idx, "student", s.name, out["text"], 9,
                                wait_ms=None, behavior=out["behavior"])
                print(f"       [{s.name}] {out['text']}   （{out['behavior']}，未点名主动发言）")
            elif out["behavior"] == "走神":
                idx += 1
                # ★ 走神不记 FIAS 类别（fias=None）—— 它是"行为事件"，不是一句课堂话语，
                #   否则会把 ST（学生话语比）算虚高。这是 FIAS 用在本场景的一个关键约定。
                store_.add_turn(sid, idx, "student", s.name, "（走神中）", None,
                                wait_ms=None, behavior="走神")
                print(f"       [{s.name}] （走神中）")
        last_was_question = is_q

    m = fias.compute_metrics(store_.turns(sid), [s.name for s in cast])
    store_.save_metrics(sid, m)
    return sid


def main() -> None:
    db = Path(__file__).parent / "kejing.db"
    if db.exists():
        db.unlink()
    st = store.Store(db)

    print("=" * 78)
    print(f"模型状态：{llm.status()}")
    print(f"教案：初中数学·负负得正　｜　虚拟学生：3 名（同一 seed，两轮配置完全一致）")
    print("=" * 78)

    cast1 = students.create_cast(YAML, count=3, seed=7)
    print("\n【虚拟学生名册】")
    for s in cast1:
        mc = s.misconception or {}
        print(f"  {s.sid} {s.name}（{s.archetype}，参与度 {s.engagement}）"
              f"　预设错误：{mc.get('name', '—')}")

    print("\n" + "-" * 78 + "\n【第一轮试讲】以讲授和指令为主，只叫一个学生，问完立刻自己接\n" + "-" * 78)
    s1 = run_round(st, cast1, ROUND1, ROUND1_WAIT, 1)
    m1 = st.metrics(s1)
    print("\n" + fias.format_metrics(m1, "第一轮结果"))

    print("\n" + "-" * 78 + "\n【第二轮试讲】同一批学生、同一份教案，老师改进了提问与等待\n" + "-" * 78)
    cast2 = students.create_cast(YAML, count=3, seed=7)   # ★ 同一 seed → 同一批学生
    s2 = run_round(st, cast2, ROUND2, ROUND2_WAIT, 2)
    m2 = st.metrics(s2)
    print("\n" + fias.format_metrics(m2, "第二轮结果"))

    print("\n" + "=" * 78 + "\n【两轮对比 —— 只比较教师纯行为指标】\n" + "=" * 78)
    print(f"  {'指标':<22}{'第一轮':>10}{'第二轮':>10}{'变化':>10}  结论")
    for label, a, b, delta, trend in store.compare(m1, m2):
        print(f"  {label:<22}{str(a):>10}{str(b):>10}{str(delta):>10}  {trend}")

    print("\n【为什么不用 TT / ST / SIR 下结论】")
    print(f"  TT {m1['TT']} → {m2['TT']}；ST {m1['ST']} → {m2['ST']}；SIR {m1['SIR']} → {m2['SIR']}")
    print("  ↑ 这三个受「我们怎么设定学生」影响，只能描述课堂情境，不能当师范生能力证据。")
    print("\n数据已落库：" + str(db))
    print("原始事件可重算 —— 这就是「评价可审计」的意思。")


if __name__ == "__main__":
    main()
