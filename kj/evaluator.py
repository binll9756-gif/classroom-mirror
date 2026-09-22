# -*- coding: utf-8 -*-
"""教学评价报告（F8）

★★ 这里的设计和"让模型读一遍对话然后写段评价"完全不同 ★★

    第一步（纯代码）：从指标和事件流里，找出【确定的、带证据的】问题
    第二步（模型）：  只负责把这些问题写成人能看懂、能执行的话

为什么？因为如果让模型自由发挥，会出现两个致命问题：
    1. 它会编出没有依据的结论（"课堂氛围不够活跃"这种空话）
    2. 同一份对话问两次，结论不一样 —— 不可复现、不可申诉

所以：**发现问题的权力在代码手里，模型只有措辞权。**
每条结论都必须挂 evidence_turn_ids，能点回原文。
"""
from __future__ import annotations

from . import fias, llm

# 教师基线（引自 NAACL 2025 论文 Table 2，仅作对照刻度）
IDR_NOVICE = 0.885
IDR_EXPERT = 1.473


# ================================================================ 第一步：代码找问题
def find_issues(lesson: dict | None, m: dict, turns: list[dict],
                roster: list[str]) -> list[dict]:
    """纯代码。每条问题都由确定的规则触发，并带上可回溯的证据行号。"""
    issues: list[dict] = []

    def add(level, kind, fact, evidence):
        issues.append({"level": level, "kind": kind, "fact": fact,
                       "evidence_turn_ids": [e for e in evidence if e is not None]})

    # --- 哪些 turn 是提问、哪些是学生答错、哪些是引导动作 ---
    q_ids = [t["idx"] for t in turns if t.get("fias") == 4 and t.get("speaker") == "teacher"]
    wrong_ids = [t["idx"] for t in turns if t.get("behavior") == "答错并坚持"]
    guide_ids = [t["idx"] for t in turns if t.get("fias") in (2, 3)
                 and t.get("speaker") == "teacher"]

    # --- 样本量检查：★ 这一条必须排在前面 ---
    # 只有 2 次提问就算出 IDR=1.5，然后说"超过专家教师水平"，是过度解读。
    # 指标本身没算错，但样本太小，不能据此下能力结论 —— 这是学术诚信问题。
    n_q = m.get("teacher_questions") or 0
    n_t = m.get("total") or 0
    small = n_q < 5 or n_t < 20
    if small:
        add("warn", "样本量偏小",
            f"本节课只有 {n_q} 次提问、{n_t} 次发言，样本量偏小。"
            f"下面的指标和评分**只能作为参考**，不能据此判断教学能力。"
            f"建议完整上一节课（提问 ≥5 次、发言 ≥20 次）再看数据。",
            q_ids[:4])

    # 1) IDR：与人类教师基线的比较
    idr = m.get("IDR")
    if idr is not None:
        if idr < 0.5:
            add("risk", "IDR 过低",
                f"你的间接引导/直接讲授比（IDR）只有 {idr}，远低于新手人类教师的 {IDR_NOVICE}"
                f"（专家为 {IDR_EXPERT}）。这一节课你主要在「讲」，很少「引」。",
                q_ids[:6] or guide_ids[:6])
        elif idr < IDR_NOVICE:
            add("warn", "IDR 偏低",
                f"你的 IDR 是 {idr}，低于新手人类教师的 {IDR_NOVICE}。可以再多用一点"
                f"「采纳学生想法」「表扬鼓励」这类间接引导。",
                q_ids[:4] or guide_ids[:4])

    # 2) 等待时间
    w = m.get("avg_wait_s")
    if w is not None:
        if w < 1.5:
            add("risk", "等待时间过短",
                f"你提问后平均只等了 {w} 秒就继续。教育学研究建议等待 ≥3 秒 —— "
                f"等得久一点，学生回答会更长、更完整。",
                q_ids[:6])
        elif w < 3:
            add("warn", "等待时间偏短",
                f"你提问后平均等待 {w} 秒，略短于建议的 3 秒。试试默数三下再叫人或接话。",
                q_ids[:4])

    # 3) 自问自答
    sar = m.get("self_answer_rate")
    if sar:
        add("risk", "自问自答",
            f"有 {int(round(sar * (m.get('teacher_questions') or 0)))} 次提问，"
            f"你没等学生回答就自己把答案说了。这样学生就失去了思考的机会。",
            q_ids[:6])

    # 4) 有学生没被点名 —— ★ 要区分「没被点名但主动发过言」和「完全没参与」
    #    例：过度积极型学生会主动抢答，他并不算被忽略，只是老师没把点名权给他。
    ignored = m.get("ignored_students") or []
    spoke_names = {t.get("name") for t in turns
                   if t.get("speaker") == "student" and t.get("text")}
    quiet, active_only = [], []
    for name in ignored:
        (active_only if name in spoke_names else quiet).append(name)

    if quiet:
        add("risk", "有学生整节课没参与",
            f"{'、'.join(quiet)} 这节课既没被叫到、也没有主动发言。"
            f"课堂参与是不公平的 —— 这类学生往往最容易掉队。",
            [])
    if active_only:
        add("warn", "有学生只靠自己主动发言",
            f"{'、'.join(active_only)} 这节课**一次都没被你点名**，"
            f"他是靠自己抢答参与的（发言 {sum(1 for t in turns if t.get('name') in active_only and t.get('speaker')=='student')} 次）。"
            f"主动的孩子也需要被老师点到 —— 否则发言权只掌握在敢抢的人手里。",
            [])

    # 5) 提问层次
    mr = m.get("memory_question_ratio")
    if mr is not None and mr >= 0.6:
        add("warn", "提问层次偏低",
            f"你的提问里有 {int(round(mr * 100))}% 是记忆型的（如「记住了吗」）。"
            f"可以多问「为什么」「你怎么想的」，把学生的思考推深一层。",
            q_ids[:6])

    # 6) 学生答错了，但你没有引导他重新想
    if wrong_ids and not guide_ids:
        add("warn", "学生答错后缺少引导",
            "这一节课有学生答错并坚持了自己的错误理解，"
            "但你全程没有出现「采纳他的想法」「表扬鼓励」这类引导动作。"
            "试试先顺着他的思路走一步，再带他发现问题。",
            wrong_ids[:4])

    # 7) 教案本身的设计问题（来自 lesson.check_alignment，纯代码判定）
    if lesson:
        for g in (lesson.get("design_gaps") or [])[:3]:
            add("warn", "教案设计问题", g, [])

    # --- 正面评价：拆成独立几条，别挤成一大段 ---
    # ★ 小样本时不夸指标，只说「动作做对了」（避免用 2 次提问推断教学能力）
    if not small:
        if idr is not None and idr >= IDR_NOVICE:
            add("good", "互动引导做得好",
                f"你的 IDR 是 {idr}，达到了新手人类教师的水平（{IDR_NOVICE}）"
                f"—— 说明你除了讲，也在引导学生说。", q_ids[:4])
        if w is not None and w >= 3:
            add("good", "等待时间达标",
                f"你提问后平均等待 {w} 秒，达到了教育学建议的 3 秒标准。"
                f"这会让学生回答得更完整。", q_ids[:4])
    else:
        if w is not None and w >= 3:
            add("good", "等待时间达标",
                f"你提问后平均等待 {w} 秒，达到了教育学建议的 3 秒标准（样本较小，仅供参考）。",
                q_ids[:4])
    if m.get("answered_questions"):
        add("good", "把回答机会交给了学生",
            f"你有 {m['answered_questions']} 次提问真的把话交给了学生，"
            f"而不是自己接话。", q_ids[:3])
    if roster and not quiet:
        add("good", "课堂参与比较公平", "没有学生整节课被完全忽略。", [])

    return issues


# ================================================================ 第二步：代码打分
def score(m: dict, roster: list[str]) -> dict:
    """纯代码打分（可复现）。每项 0~5 分；无法计算的项给 None，不计入综合分。

    ★ 注意 IDR 的语义：如果老师一句直接讲授都没有（全是提问），
      IDR 会因为除零而无法计算 —— 那是「无法判定」，不是「0 分」。
      把它算成 0 分会冤枉一位只提问的老师。
    """
    def clamp(x):
        return round(max(0.0, min(5.0, x)), 1)

    idr = m.get("IDR")
    w = m.get("avg_wait_s")
    mr = m.get("memory_question_ratio")
    ignored = len(m.get("ignored_students") or [])

    s = {
        "互动引导": clamp(idr / IDR_EXPERT * 5) if idr is not None else None,
        "等待时间": clamp((w if w is not None else 0) / 3 * 5) if w is not None else None,
        "叫答公平": clamp(5 - ignored * 2.5),
        "提问质量": clamp((1 - (mr if mr is not None else 0)) * 5),
    }
    valid = [v for v in s.values() if v is not None]
    s["综合"] = round(sum(valid) / len(valid), 1) if valid else None
    return s


# ================================================================ 第三步：模型只负责措辞
_ADVICE_SYS = """你是一位耐心的教学法指导老师，正在给一位师范生写试讲反馈。

【你必须遵守】
1. 只根据我给你的「客观事实」来写，不要自己新增结论，不要编造没发生的事。
2. ★ 数字必须来自事实：不许改动事实里的数字，也不许自己发明新数字。
   例如事实说「建议等待 ≥3 秒」，你就只能写 3 秒，不能写成 5 秒或三十秒。
   如果建议需要一个时长/次数，就用事实里已经出现过的那个数字。
3. 每条建议写成三段式：① 指出行为（引用事实）② 说明教育上的原因 ③ 给一个下一轮能立刻执行的动作。
4. 语气对事不对人，具体、可执行。禁止说「你可以更关注学生」这类空话。
5. 每条不超过 80 字。只输出 JSON。"""


def _advice_batch(issues: list[dict]) -> list[str]:
    """★ 一次性把所有建议生成完（而不是每条调一次模型）。

    实测：逐条生成 3 条约 6s+；批量一次约 3.2s。
    报告里有 4~6 条诊断，批量能省十几秒 —— 这是"结束试讲"卡顿的主因。

    任何异常都退回「直接使用客观事实」，保证报告一定能出来。
    """
    fallback = [i["fact"] for i in issues]
    todo = [(k, i) for k, i in enumerate(issues) if i["level"] != "good"]
    if not todo or not llm.available():
        return fallback

    numbered = "\n".join(f"事实{k+1}：{i['fact']}" for k, i in todo)
    user = (f"下面有 {len(todo)} 条客观事实，请为每条写一句改进建议（每条 ≤60 字）。\n"
            f"输出 JSON：{{\"advices\": [\"建议1\", \"建议2\", ...]}}\n"
            f"顺序必须与事实一致，共 {len(todo)} 条。\n\n{numbered}")
    obj = llm.chat_json(_ADVICE_SYS, user, temperature=0.4,
                        max_tokens=200 + 160 * len(todo))
    advs = obj.get("advices") if isinstance(obj, dict) else None
    if not isinstance(advs, list) or len(advs) != len(todo):
        return fallback

    out = list(fallback)
    for (k, _i), a in zip(todo, advs):
        a = str(a).strip()
        if a:
            out[k] = a
    return out


def build_report(lesson: dict | None, m: dict, turns: list[dict],
                 roster: list[str], write_advice: bool = True) -> dict:
    """生成完整报告：代码发现的问题 + 代码打的分数 + 模型润色的建议。"""
    issues = find_issues(lesson, m, turns, roster)
    advices = _advice_batch(issues) if write_advice else [i["fact"] for i in issues]
    for it, adv in zip(issues, advices):
        it["advice"] = adv
    return {
        "scores": score(m, roster),
        "metrics": m,
        "issues": issues,
        "risk_flags": [i["fact"] for i in issues if i["level"] == "risk"],
        "small_sample": any(i.get("kind") == "样本量偏小" for i in issues),
        "roster": roster,
    }


# ================================================================ 渲染
_ICON = {"risk": "🔴", "warn": "⚠️", "good": "✅"}


def format_report(rep: dict) -> str:
    lines = []
    s = rep["scores"]

    def shown(v):
        return "—（无法计算）" if v is None else str(v)

    lines.append("  【评分（由代码计算，可复现）】")
    lines.append("   " + "　".join(f"{k} {shown(v)}" for k, v in s.items() if k != "综合"))
    tail = "　⚠️ 样本量小，仅作参考，不能据此判断教学能力" if rep.get("small_sample") else ""
    lines.append(f"   综合 {shown(s.get('综合'))} / 5.0{tail}")
    lines.append("")
    lines.append("  【诊断结论】")
    for it in rep["issues"]:
        icon = _ICON.get(it["level"], "·")
        ev = f"　[证据 turn {it['evidence_turn_ids']}]" if it["evidence_turn_ids"] else ""
        lines.append(f"   {icon} {it['advice']}{ev}")
    lines.append("")
    lines.append("  ⚠️ 本报告为发展性评价：只描述教学行为，不出具教师能力结论；")
    lines.append("     每条结论都可由事件流重算复核，最终解释权归指导教师。")
    return "\n".join(lines)
