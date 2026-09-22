# -*- coding: utf-8 -*-
"""课镜 · 交互式试讲（命令行版）

这是「正常使用这个智能体」的入口。跑法：

    python run_teach.py

你会对着 3 个 AI 学生试讲。它们是本地模型生成的（数据不出本机）。

★ 等待时间是怎么测的（这一点很重要）：
    你提问之后，系统会停一下问你「让谁答？」。
    你停顿多久才把话交出去 —— 这就是教育学里的「等待时间」。
      · 按回车    → 交给学生答（停顿时长记为等待时间）
      · 直接打字  → 你自己接着说（记为「抢答」，而且这个提问就作废了）
    停顿不足 1.5 秒也计入抢答率。研究建议等待 ≥3 秒。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kj import evaluator, fias, lesson as lesson_mod, llm, store, students  # noqa: E402

YAML = Path(__file__).parent / "knowledge" / "misconceptions.yaml"
DB = Path(__file__).parent / "kejing.db"
Q_WORDS = ("吗", "呢", "为什么", "怎么", "多少", "谁", "什么", "？", "?")

HELP = """
────────────────────────────── 可用指令 ──────────────────────────────
  直接打字            老师说话（正常试讲）
  /叫 小林            下一句指定点名小林（小周 / 阿豪 同理）
  /走神               让某个学生走神（记为行为事件，不进 FIAS 九类）
  /状态               查看三个学生的认知状态
  /报告               结束本轮，出 FIAS 指标与诊断报告
  /帮助               显示这份帮助
  /退出               结束
──────────────────────────────────────────────────────────────────────
  小提示：想练「等待时间」，提问后停顿 3 秒再回车 —— 看报告里的数字变化。
──────────────────────────────────────────────────────────────────────
"""


def is_question(text: str) -> bool:
    return any(w in text for w in Q_WORDS)


def pick_target(text: str, cast: list[students.Student],
                forced: str | None, called: set[str]) -> students.Student:
    """谁来回话：优先点名 → 句子里提到的名字 → 还没被叫过的（避免总叫同一个）"""
    if forced:
        for s in cast:
            if s.name == forced:
                return s
    for s in cast:
        if s.name in text:
            return s
    for s in cast:
        if s.name not in called:
            return s
    return cast[0]


def show_states(cast: list[students.Student]) -> None:
    for s in cast:
        st = s.state
        print(f"   {s.name}（{s.archetype}）理解 {st['understanding']:.2f}｜"
              f"困惑 {st['confusion']:.2f}｜耐心 {st['patience']}｜"
              f"已被说服 {'是' if st['convinced'] else '否'}")


def run_round(round_no: int, cast: list[students.Student], st: store.Store,
              lesson: dict) -> tuple[int, dict]:
    sid = st.new_session(lesson=(lesson.get("topic") or "未命名课时"), round_no=round_no,
                         note=f"命令行交互试讲（{llm.status()}）")
    idx = 0
    called: set[str] = set()
    forced: str | None = None

    print(f"\n{'='*72}\n 第 {round_no} 轮试讲开始\n{'='*72}")
    print(" 开始讲课吧。输入 /帮助 看指令。\n")

    while True:
        try:
            line = input("你（老师）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        # ------------------------------ 指令
        if line == "/帮助":
            print(HELP); continue
        if line == "/状态":
            show_states(cast); continue
        if line in ("/退出",):
            print("已结束。"); sys.exit(0)
        if line in ("/报告", "/二轮"):
            break
        if line.startswith("/叫"):
            forced = line[2:].strip()
            print(f"   （下一句会点 {forced}）"); continue
        if line == "/走神":
            s = [x for x in cast if x.name not in called] or cast
            s = s[-1]
            idx += 1
            st.add_turn(sid, idx, "student", s.name, "（走神中）", None, behavior="走神")
            print(f"   [{s.name}] （走神中）—— 已记为行为事件")
            continue

        # ------------------------------ 教师说话
        target_name = forced
        forced = None
        if target_name:
            called.add(target_name)

        asked = is_question(line)
        f = fias.rule_classify("teacher", line)
        idx += 1
        st.add_turn(sid, idx, "teacher", "师范生", line, f, target=target_name)

        spoke_name: str | None = None

        if asked:
            t0 = time.time()
            print("   ⏱  让谁答？（回车 = 交给学生；输入内容 = 你自己接着说）")
            try:
                nxt = input("      > ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); break
            wait_ms = int((time.time() - t0) * 1000)

            if nxt:
                # 老师自问自答 → 记抢答，学生这次没机会说话
                idx += 1
                st.add_turn(sid, idx, "teacher", "师范生", nxt,
                            fias.rule_classify("teacher", nxt), wait_ms=wait_ms)
                print(f"   ⚠️ 你自问自答了（等待 {wait_ms/1000:.1f} 秒）——已记为抢答")
            else:
                stu = pick_target(line, cast, target_name, called)
                called.add(stu.name)
                out = stu.act(line, is_question=True, is_called=True)
                idx += 1
                st.add_turn(sid, idx, "student", stu.name, out["text"] or "（沉默）", 8,
                            wait_ms=wait_ms, behavior=out["behavior"])
                spoke_name = stu.name
                print(f"   [{stu.name}] {out['text']}　（{out['behavior']}，等待 {wait_ms/1000:.1f}s）")

        # ------------------------------ 未被点名的其他学生
        # 只允许「抢答 / 走神」两种行为；被提问的回答只能由上一位学生给出。
        for s in cast:
            if s.name == spoke_name:
                continue
            out = s.act(line, is_question=False, is_called=False)
            if out["text"] and out["behavior"] == "抢答":
                idx += 1
                st.add_turn(sid, idx, "student", s.name, out["text"], 9, behavior="抢答")
                print(f"   [{s.name}] {out['text']}　（主动抢答）")
            elif out["behavior"] == "走神":
                idx += 1
                st.add_turn(sid, idx, "student", s.name, "（走神中）", None, behavior="走神")
                print(f"   [{s.name}] （走神中）")

    turns = st.turns(sid)
    roster = [s.name for s in cast]
    m = fias.compute_metrics(turns, roster)
    st.save_metrics(sid, m)
    print("\n" + fias.format_metrics(m, f"第 {round_no} 轮结果"))
    print()
    rep = evaluator.build_report(lesson, m, turns, roster)
    print(evaluator.format_report(rep))
    return sid, rep


def main() -> None:
    print("=" * 72)
    print(" 课镜 · AI 微格教学教练（命令行版）")
    print("=" * 72)
    print(f" 模型：{llm.status()}")
    if not llm.available():
        print(" ⚠️ 模型不可用，学生台词将用模板兜底（FIAS 指标仍真实计算）")

    # ---- 教案：给了文件路径就解析文件，否则用内置示例 ----
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        p = Path(args[0])
        print(f" 正在解析教案：{p.name} …")
        lesson = lesson_mod.analyze(lesson_mod.load_text(p)) or lesson_mod.demo_lesson()
        print(f" 解析完成：{lesson.get('topic')}")
    else:
        lesson = lesson_mod.demo_lesson()
        print(f" 教案（内置示例）：{lesson.get('topic')}")
    print("\n【教案分析结果】")
    for o in lesson.get("objectives", []):
        print(f"   目标 {o['id']}｜{o.get('text')}　[{(o.get('level') or '')}]")
    for mc in lesson.get("likely_misconceptions", []):
        print(f"   学生可能这样错 —— {mc.get('name')}：{(mc.get('student_says') or [''])[0]}")
    for g in (lesson.get("design_gaps") or []):
        print(f"   ⚠️ 教案薄弱点：{g}")
    print(HELP)

    if DB.exists():
        DB.unlink()
    st = store.Store(DB)
    cast = students.create_cast(YAML, 3, seed=7)
    print("【本次课堂的虚拟学生】")
    for s in cast:
        mc = s.misconception or {}
        print(f"   {s.sid} {s.name}（{s.archetype}，参与度 {s.engagement}）"
              f"　预设错误：{mc.get('name','—')}")

    s1, _rep1 = run_round(1, cast, st, lesson)

    print("\n" + "─" * 72)
    try:
        ans = input(" 要不要再讲第二轮？同一批学生、同一份教案（y/N）> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans == "y":
        print(" 建议：先根据上面的报告改教案，再讲第二轮。\n")
        cast2 = students.create_cast(YAML, 3, seed=7)      # ★ 同一 seed → 同一批学生
        s2, _rep2 = run_round(2, cast2, st, lesson)
        m1, m2 = st.metrics(s1), st.metrics(s2)
        print("\n" + "=" * 72 + "\n【两轮对比 —— 只比较教师纯行为指标】\n" + "=" * 72)
        print(f"  {'指标':<24}{'第一轮':>10}{'第二轮':>10}{'变化':>10}  结论")
        for label, a, b, delta, trend in store.compare(m1, m2):
            print(f"  {label:<24}{str(a):>10}{str(b):>10}{str(delta):>10}  {trend}")
        print("\n ⚠️ TT/ST/SIR 受「AI 学生被设定得多爱说话」影响，不作为教师能力证据。")

    print(f"\n数据已落库：{DB}")
    print("原始事件可重算 —— 这就是「评价可审计」的意思。")


if __name__ == "__main__":
    main()
