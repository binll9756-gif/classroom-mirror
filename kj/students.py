# -*- coding: utf-8 -*-
"""虚拟学生：画像 + 认知状态机 + 发言生成

★ 本项目最关键的一处设计（也是最难的一处）：
      状态由【代码】维护，台词由【模型】生成。
  为什么？如果不这样做，会出现一个很顽固的问题：
      "上一轮学生答错了，下一轮被追问就突然答对了。"
  这会让训练价值归零 —— 师范生正在练"怎么纠正一个错误理解"，结果学生自己好了。
  所以：学生能不能"变懂"，由代码的状态机决定，不由模型的语气决定。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import llm

# 四种原型：每一种都有明确的教育训练目的
ARCHETYPES = {
    "迷思型": "坚定地给出错误答案，并用错误逻辑解释；被追问后仍然坚持",
    "沉默型": "不主动发言，被点名才回应，容易走神",
    "过度积极型": "抢答、跑题、占用课堂时间",
}


@dataclass
class Student:
    sid: str
    name: str
    archetype: str
    misconception: dict | None
    engagement: float = 0.6           # 0~1 参与度基线（代码参数，不是模型决定的）
    max_sentence_len: int = 18
    forbidden_words: list[str] = field(default_factory=list)
    # 认知状态：只由代码改，模型不许直接改
    state: dict = field(default_factory=lambda: {
        "understanding": 0.2, "confusion": 0.7, "convinced": False, "patience": 2,
    })
    _turn: int = 0
    _distract_cd: int = 0          # 走神冷却：刚走神过，接下来几轮不再走神（减少噪声，也更像真人）
    _rn: random.Random = field(default_factory=lambda: random.Random(0))

    # ---------------------------------------------------------------- 发言
    def reply(self, teacher_text: str, is_question: bool, is_called: bool) -> dict:
        """返回 {'text': str|None, 'behavior': str, 'state_delta': dict}"""
        self._turn += 1
        st = self.state
        if self._distract_cd > 0:
            self._distract_cd -= 1

        # ① 是否走神：由【代码】按参与度决定，不由模型自觉（保证可复现）
        if (self.archetype == "沉默型" and self._distract_cd == 0
                and self._rn.random() > self.engagement and not is_called):
            self._distract_cd = 4
            st["confusion"] = min(1.0, st["confusion"] + 0.05)
            return {"text": None, "behavior": "走神", "state_delta": {"confusion": +0.05}}

        # ② 未被提问且不是被点名 → 只有"过度积极型"会主动抢话
        if not is_question and not is_called:
            if self.archetype == "过度积极型" and self._rn.random() < 0.5:
                return {"text": "老师我知道！是不是等于正数？", "behavior": "抢答",
                        "state_delta": {}}
            return {"text": None, "behavior": "静听", "state_delta": {}}

        # ③ 被提问：只要"未被说服"，就一定答错并坚持（这是反事实学生的核心）
        if not st["convinced"]:
            says = (self.misconception or {}).get("student_says") or ["我不知道……"]
            line = says[(self._turn - 1) % len(says)]
            st["patience"] -= 1
            # 只有"耐心耗尽 + 老师确实做了引导(采纳想法/表扬)"，理解度才上升
            if st["patience"] <= 0 and ("按你说的" in teacher_text or "举个例子" in teacher_text):
                st["understanding"] = min(1.0, st["understanding"] + 0.3)
                st["confusion"] = max(0.0, st["confusion"] - 0.3)
                if st["understanding"] >= 0.6:
                    st["convinced"] = True
            return {"text": line, "behavior": "答错并坚持",
                    "state_delta": {"patience": -1}}

        return {"text": "哦……我明白了，因为乘 -1 相当于换个方向！", "behavior": "理解",
                "state_delta": {}}

    # ---------------------------------------------------------------- 提示词
    def system_prompt(self) -> str:
        mc = self.misconception or {}
        return f"""你是一名{_grade_band()}学生，名字叫{self.name}，{self.archetype}。

【你的人设】{ARCHETYPES[self.archetype]}
【你的认知状态】理解程度 {self.state['understanding']:.1f}，困惑程度 {self.state['confusion']:.1f}，
             是否被说服：{'是' if self.state['convinced'] else '否'}
【你根深蒂固的错误理解】{mc.get('name', '（无）')}：{mc.get('student_says', [''])[0] if mc.get('student_says') else ''}

【必须遵守的硬规则】
1. 你就是个学生，不是老师：不许总结教学内容，不许讲得比老师好，不许用学术词。
2. 句子要短，不超过 {self.max_sentence_len} 个字；禁止使用这些词：{', '.join(self.forbidden_words)}。
3. 你的错误必须来自上面那条错误理解，答错后【至少要再坚持一轮】，不能因为老师讲了一遍就马上变懂。
4. 不确定的时候要表现出不确定（"呃……是不是……"）。
5. 只输出你要说的话本身，不要加任何解释、旁白或标点以外的东西。"""


def _grade_band() -> str:
    return "初中"


# ---------------------------------------------------------------- 铸造工坊
def create_cast(yaml_path: str | Path, count: int = 3, seed: int = 7) -> list[Student]:
    """生成一组虚拟学生。同一份教案 + 同一个 seed → 每次都生成同一批学生（可复现）。"""
    data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    mcs = data.get("misconceptions", [])
    lp = data.get("language_profile", {})
    rn = random.Random(seed)

    plan = [
        ("S1", "小林", "迷思型", 0.55),
        ("S2", "小周", "沉默型", 0.30),
        ("S3", "阿豪", "过度积极型", 0.85),
    ][:count]

    cast = []
    for i, (sid, name, arch, eng) in enumerate(plan):
        mc = mcs[i % len(mcs)] if mcs else None
        cast.append(Student(
            sid=sid, name=name, archetype=arch, misconception=mc, engagement=eng,
            max_sentence_len=lp.get("max_sentence_len", 18),
            forbidden_words=lp.get("forbidden_words", []),
            _rn=random.Random(seed * 100 + i),
        ))
    return cast


def student_line_online(stu: Student, teacher_text: str) -> str | None:
    """有 Key 时用真模型生成台词；没有则返回 None（调用方用模板）"""
    return llm.chat(stu.system_prompt(), f"老师对你说：{teacher_text}\n请以你的身份回应。", 0.9)
