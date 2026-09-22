# -*- coding: utf-8 -*-
"""事件流存储（SQLite）

★ 设计要点：原始记录只追加、不修改；指标是从原始记录重算出来的。
  这样评委问"这个分数怎么来的"，你能从第一句话开始重算给他看。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lesson TEXT, round_no INTEGER, note TEXT, created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS turn (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER, idx INTEGER,
  speaker TEXT, name TEXT, text TEXT, fias INTEGER,
  wait_ms INTEGER, target TEXT, behavior TEXT
);
CREATE TABLE IF NOT EXISTS metric (
  session_id INTEGER PRIMARY KEY, payload TEXT
);
"""


class Store:
    def __init__(self, path: str | Path = "kejing.db"):
        self.con = sqlite3.connect(str(path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def new_session(self, lesson: str, round_no: int, note: str = "") -> int:
        cur = self.con.execute(
            "INSERT INTO session(lesson, round_no, note) VALUES(?,?,?)", (lesson, round_no, note))
        self.con.commit()
        return int(cur.lastrowid)

    def add_turn(self, session_id: int, idx: int, speaker: str, name: str, text: str,
                 fias: int, wait_ms: int | None = None, target: str | None = None,
                 behavior: str = "") -> None:
        self.con.execute(
            "INSERT INTO turn(session_id,idx,speaker,name,text,fias,wait_ms,target,behavior)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, idx, speaker, name, text, fias, wait_ms, target, behavior))
        self.con.commit()

    def turns(self, session_id: int) -> list[dict]:
        rows = self.con.execute(
            "SELECT idx,speaker,name,text,fias,wait_ms,target,behavior FROM turn"
            " WHERE session_id=? ORDER BY idx", (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def save_metrics(self, session_id: int, metrics: dict) -> None:
        self.con.execute("INSERT OR REPLACE INTO metric(session_id,payload) VALUES(?,?)",
                         (session_id, json.dumps(metrics, ensure_ascii=False)))
        self.con.commit()

    def metrics(self, session_id: int) -> dict:
        row = self.con.execute("SELECT payload FROM metric WHERE session_id=?",
                               (session_id,)).fetchone()
        return json.loads(row["payload"]) if row else {}


def compare(m1: dict, m2: dict) -> list[tuple]:
    """两轮对比：只比较【教师纯行为指标】——TT/ST/SIR 不参与结论（见 fias.py 说明）"""
    rows = []
    for key, label, better in [
        ("IDR", "IDR 间接/直接影响比", "up"),
        ("avg_wait_s", "平均等待时间(秒)", "up"),
        ("self_answer_rate", "自问自答率", "down"),
        ("memory_question_ratio", "记忆型提问占比", "down"),
        ("teacher_questions", "教师提问次数", "up"),
        ("answered_questions", "学生获得回答机会次数", "up"),
    ]:
        a, b = m1.get(key), m2.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            delta = round(b - a, 3)
            if delta == 0:
                trend = "持平"
            else:
                good = (delta > 0) if better == "up" else (delta < 0)
                trend = "↑ 改善" if good else "↓ 变差"
        else:
            delta, trend = None, "—"
        rows.append((label, a, b, delta, trend))
    ai, bi = m1.get("ignored_students", []), m2.get("ignored_students", [])
    rows.append(("被忽略学生数", len(ai), len(bi), len(bi) - len(ai),
                 "↑ 改善" if len(bi) < len(ai) else ("持平" if len(bi) == len(ai) else "↓ 变差")))
    return rows
