# -*- coding: utf-8 -*-
"""虚拟学生：画像 + 认知状态机 + 台词生成

★★ 本项目最关键的一处设计，也是全项目最难的地方 ★★

    代码决定"这一轮做什么"（答题 / 抢答 / 走神 / 沉默 / 终于懂了）
    模型只负责"把这句话说出来"
    状态由代码更新 —— 模型不许碰

为什么必须这样？如果不这么做，会出现一个很顽固的问题：

    "上一轮学生答错了，下一轮被追问就突然答对了。"

师范生正在练的就是"怎么纠正一个错误理解"——学生自己好了，训练价值归零。
把"懂没懂"从模型手里拿走，是唯一的解法。（纯靠提示词压不住。）

另外一个坑：模型默认语域是"知识渊博的成年人"，会说出
    "从数学本质上看，负负得正体现了……"
所以台词要过一道质量闸（句长、禁用词、不许自称老师），不合格就退回模板。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import llm

ARCHETYPES = {
    "迷思型": "坚定地给出错误答案，并用错误逻辑解释；被追问后仍然坚持",
    "沉默型": "不主动发言，被点名才回应，容易走神",
    "过度积极型": "抢答、跑题、占用课堂时间",
}

# 台词质量闸用的"老师腔"特征词
_TEACHERISH = ("综上所述", "本质", "认知", "维度", "由此可见", "教学", "知识点归纳", "我们来总结")

_ACTION_INSTRUCTION = {
    "answer": "你要用自己那句错误理解来回答老师。允许你有点犹豫、有点倔。**绝对不要说正确答案**。",
    "blurt": "你要抢在别人前面插一句话（跑题、抢答、或自作聪明），一句话就够。",
    "convinced": "你刚刚终于想通了。用一句很短的话说出你明白的那一点，语气像松了口气。",
    "recall": "老师要你复述刚才讲的内容，你就照你记得的说（可能记岔了）。",
}


@dataclass
class Student:
    sid: str
    name: str
    archetype: str
    misconception: dict | None
    engagement: float = 0.6           # 0~1 参与度基线（代码参数，不是模型决定）
    max_sentence_len: int = 18
    forbidden_words: list[str] = field(default_factory=list)
    grade_band: str = "初中"
    # 认知状态：只由代码改
    state: dict = field(default_factory=lambda: {
        "understanding": 0.2, "confusion": 0.7, "convinced": False, "patience": 2,
    })
    _turn: int = 0
    _distract_cd: int = 0             # 走神冷却：刚走神过，接下来几轮不再走神
    _rn: random.Random = field(default_factory=lambda: random.Random(0))
    last_source: str = "template"     # 'llm' | 'template' —— 便于排查

    # ================================================================ 对外主入口
    def act(self, teacher_text: str, is_question: bool, is_called: bool,
            use_llm: bool = True) -> dict:
        """走一格。返回 {'text': str|None, 'behavior': str, 'state_delta': dict, 'source': str}"""
        self._turn += 1
        if self._distract_cd > 0:
            self._distract_cd -= 1

        kind, behavior = self._decide(teacher_text, is_question, is_called)

        # ---- 生成台词：优先真模型，失败退回模板 ----
        text, source = None, "template"
        if kind in _ACTION_INSTRUCTION:
            if use_llm and llm.available():
                got = self._llm_line(kind, teacher_text)
                if got:
                    text, source = got, "llm"
            if not text:
                text, source = self._template_line(kind), "template"
        self.last_source = source

        delta = self._apply_state(kind, teacher_text)
        return {"text": text, "behavior": behavior, "state_delta": delta, "source": source}

    # 兼容旧接口（离线/测试用）
    def reply(self, teacher_text: str, is_question: bool, is_called: bool) -> dict:
        return self.act(teacher_text, is_question, is_called, use_llm=False)

    # ================================================================ ① 代码决定行为
    def _decide(self, teacher_text: str, is_question: bool, is_called: bool) -> tuple[str, str]:
        st = self.state

        # 走神：由代码按参与度决定（保证可复现，不由模型自觉）
        if (self.archetype == "沉默型" and self._distract_cd == 0
                and self._rn.random() > self.engagement and not is_called):
            self._distract_cd = 4
            return "distract", "走神"

        # 已经想通了
        if st["convinced"]:
            return ("answer" if (is_question or is_called) else "silent"), "理解"

        # 没被点名也不是提问 → 只有"过度积极型"会主动插话
        if not is_question and not is_called:
            if self.archetype == "过度积极型" and self._rn.random() < 0.5:
                return "blurt", "抢答"
            return "silent", "静听"

        # 被提问 → 复述类指令走 recall，其余走 answer
        if any(k in teacher_text for k in ("重复", "复述", "说说我刚才", "记住我刚才")):
            return "recall", "复述"
        return "answer", "答错并坚持"

    # ================================================================ ② 台词：模板
    def _template_line(self, kind: str) -> str | None:
        if kind in ("silent", "distract"):
            return None
        if kind == "blurt":
            return "老师我知道！是不是等于正数？"
        if kind == "convinced":
            return "哦……我明白了，因为乘 -1 相当于换个方向！"
        says = (self.misconception or {}).get("student_says") or ["呃……我不太确定……"]
        return says[(self._turn - 1) % len(says)]

    # ================================================================ ③ 台词：真模型
    def _llm_line(self, kind: str, teacher_text: str) -> str | None:
        # ★ 性能关键：system 只放【稳定内容】，变动内容（认知状态）放进 user。
        #   这样同一个学生的 system 每轮逐字节相同 → Ollama 的 KV 缓存能命中 → 快很多。
        user = (f"{self.state_block()}\n\n"
                f"老师对你说：{teacher_text}\n\n"
                f"【这一轮你要做的】{_ACTION_INSTRUCTION[kind]}\n"
                f"【只输出你要说的那句话本身，不要引号、不要旁白、不要解释】")
        raw = llm.chat(self.system_prompt(), user, temperature=0.9,
                       max_tokens=80, retries=1)
        if not raw:
            return None
        line = self._clean(raw)
        if self._passes_guard(line):
            return line
        # 不合格 → 加严要求重试一次
        raw2 = llm.chat(self.system_prompt(),
                        user + f"\n（注意：上次太长了或用了不该用的词。这次必须 ≤{self.max_sentence_len} 个字。）",
                        temperature=0.7, max_tokens=60, retries=0)
        line2 = self._clean(raw2 or "")
        return line2 if self._passes_guard(line2) else None

    def _clean(self, text: str) -> str:
        t = text.strip()
        t = re.sub(r"^(学生|我|" + re.escape(self.name) + r")\s*[:：]\s*", "", t)  # 去掉自称前缀
        t = t.strip('"“”「」\'').strip()                                          # 去掉包裹引号
        t = t.split("\n")[0].strip()                                              # 只取第一行
        return t

    def _passes_guard(self, line: str) -> bool:
        """台词质量闸：太长 / 带老师腔 / 带禁用词 → 不合格，退回模板"""
        if not line or len(line) > self.max_sentence_len * 2.5:
            return False
        if any(w in line for w in _TEACHERISH):
            return False
        if any(w and w in line for w in self.forbidden_words):
            return False
        return True

    # ================================================================ ④ 状态：只由代码改
    def _apply_state(self, kind: str, teacher_text: str) -> dict:
        st = self.state
        delta: dict = {}
        if kind == "distract":
            st["confusion"] = min(1.0, st["confusion"] + 0.05)
            delta["confusion"] = +0.05
        elif kind == "answer" and not st["convinced"]:
            st["patience"] -= 1
            delta["patience"] = -1
            # 只有「耐心耗尽 + 老师确实做了引导动作」才允许理解度上升
            guided = any(k in teacher_text for k in ("按你说的", "举个例子", "没关系", "慢慢来", "你很棒"))
            if st["patience"] <= 0 and guided:
                st["understanding"] = min(1.0, st["understanding"] + 0.35)
                st["confusion"] = max(0.0, st["confusion"] - 0.35)
                delta["understanding"] = +0.35
                if st["understanding"] >= 0.6:
                    st["convinced"] = True
                    delta["convinced"] = True
        return delta

    # ================================================================ 提示词
    def system_prompt(self) -> str:
        """★ 只包含【稳定内容】—— 不要放会变的状态，否则 Ollama 缓存失效、每轮都慢。

        变动内容请放在 state_block() 里，随 user 消息一起发。
        """
        mc = self.misconception or {}
        says = mc.get("student_says") or []
        return f"""你是一名{self.grade_band}学生，名字叫{self.name}。

【你的人设】{ARCHETYPES[self.archetype]}
【你根深蒂固的错误理解】{mc.get('name', '（无）')}　{('，例如你会说：' + says[0]) if says else ''}

【必须遵守的硬规则】
1. 你就是个{self.grade_band}学生，不是老师：不许总结教学内容、不许讲得比老师好、不许用学术词。
2. 说话要短，一般不超过 {self.max_sentence_len} 个字。
3. 你的错误必须来自上面那条错误理解。
4. 不确定时要表现出不确定（"呃……""是不是……"）。
5. 只输出你要说的话本身，不要引号、不要旁白、不要解释。"""

    def state_block(self) -> str:
        """每轮会变的内容，随 user 消息发送。"""
        st = self.state
        return (f"【你此刻的认知状态】理解程度 {st['understanding']:.1f}；"
                f"困惑程度 {st['confusion']:.1f}；"
                f"是否已被说服：{'是' if st['convinced'] else '否'}。"
                f"只要'是否已被说服'是'否'，你就不能突然明白。")


# ================================================================ 铸造工坊
_DEFAULT_PLAN = [
    ("S1", "小林", "迷思型", 0.55),
    ("S2", "小周", "沉默型", 0.30),
    ("S3", "阿豪", "过度积极型", 0.85),
]


def create_cast(yaml_path: str | Path, count: int = 3, seed: int = 7) -> list[Student]:
    """生成一组虚拟学生。同一份教案 + 同一个 seed → 每次都是同一批（可复现）。"""
    data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    mcs = data.get("misconceptions", [])
    lp = data.get("language_profile", {})
    grade = data.get("grade_band", "初中")

    cast = []
    for i, (sid, name, arch, eng) in enumerate(_DEFAULT_PLAN[:count]):
        cast.append(Student(
            sid=sid, name=name, archetype=arch,
            misconception=mcs[i % len(mcs)] if mcs else None,
            engagement=eng,
            max_sentence_len=lp.get("max_sentence_len", 18),
            forbidden_words=lp.get("forbidden_words", []),
            grade_band=grade,
            _rn=random.Random(seed * 100 + i),
        ))
    return cast
