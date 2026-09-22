# -*- coding: utf-8 -*-
"""课镜 · Web 服务（FastAPI）

给评委用的可访问界面。跑法：

    python run_web.py
    然后浏览器打开 http://127.0.0.1:8000

★ 等待时间怎么测（和命令行版一致，这是本项目的一个关键测量）：
    老师提交一句提问后，服务端【不立刻】让学生回答，而是进入「等待」状态。
      · 点「让学生回答」 → 等待时长 = 从提问到点击的间隔
      · 直接继续打字     → 记为「自问自答」（老师没把话交出去）
    界面会显示一个计时器，让师范生直观看到自己等了多久。
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from kj import evaluator, fias, lesson as lesson_mod, llm, store, students

ROOT = Path(__file__).resolve().parent.parent
YAML = ROOT / "knowledge" / "misconceptions.yaml"
INDEX = ROOT / "web" / "index.html"
Q_WORDS = ("吗", "呢", "为什么", "怎么", "多少", "谁", "什么", "？", "?")

app = FastAPI(title="课镜 · AI 微格教学教练")

# 内存里的会话状态（演示够用；要持久化就换成数据库）
SESSIONS: dict[str, dict] = {}


def is_question(text: str) -> bool:
    return any(w in text for w in Q_WORDS)


def pick_target(text: str, cast: list[students.Student],
                forced: str | None, called: set[str]) -> students.Student:
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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if not INDEX.exists():
        return "<h1>缺少 web/index.html</h1>"
    return INDEX.read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "model": llm.status(), "model_available": llm.available()}


@app.get("/api/lesson")
def api_lesson() -> dict:
    """返回内置示例教案的解析结果（纯代码对齐检查，不需要模型）"""
    return lesson_mod.demo_lesson()


class CastReq(BaseModel):
    seed: int = 7


@app.post("/api/cast")
def api_cast(req: CastReq) -> dict:
    """新建一次试讲：生成一批虚拟学生"""
    sid = uuid.uuid4().hex[:8]
    cast = students.create_cast(YAML, 3, seed=req.seed)
    db_sid = store.Store(ROOT / "kejing_web.db").new_session(
        lesson="初中数学·负负得正", round_no=1, note="web")
    SESSIONS[sid] = {
        "cast": cast, "turns": [], "idx": 0, "called": set(),
        "db_sid": db_sid, "seat": {s.name: i for i, s in enumerate(cast)},
        "pending": False, "t0": 0.0,
    }
    return {
        "session": sid,
        "students": [
            {"name": s.name, "archetype": s.archetype, "engagement": s.engagement,
             "misconception": (s.misconception or {}).get("name"),
             "says": ((s.misconception or {}).get("student_says") or [""])[0]}
            for s in cast
        ],
    }


class SayReq(BaseModel):
    session: str
    text: str
    target: str | None = None


@app.post("/api/say")
def api_say(req: SayReq) -> dict:
    """老师说话。若上一句提问还没交出去，这句就记为「自问自答」。"""
    st = SESSIONS.get(req.session)
    if not st:
        raise HTTPException(404, "会话不存在")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "内容为空")

    db = store.Store(ROOT / "kejing_web.db")
    out: dict = {"teacher": [], "students": [], "need_yield": False}

    # 如果上一条提问还挂着 → 这次说话就是自问自答
    if st["pending"]:
        wait_ms = int((time.time() - st["t0"]) * 1000)
        st["idx"] += 1
        prev = st["turns"][-1]
        prev["wait_ms"] = wait_ms
        db.add_turn(st["db_sid"], st["idx"], "teacher", "师范生", text,
                    fias.rule_classify("teacher", text), wait_ms=wait_ms)
        st["turns"].append({"idx": st["idx"], "speaker": "teacher", "name": "师范生",
                            "text": text, "fias": fias.rule_classify("teacher", text),
                            "wait_ms": wait_ms, "behavior": ""})
        out["teacher"].append({"text": text, "self_answered": True, "wait_ms": wait_ms,
                               "note": f"⚠️ 你自问自答了（等待 {wait_ms/1000:.1f} 秒）"})
        st["pending"] = False

    f = fias.rule_classify("teacher", text)
    target = req.target or None
    if target:
        st["called"].add(target)
    st["idx"] += 1
    db.add_turn(st["db_sid"], st["idx"], "teacher", "师范生", text, f, target=target)
    st["turns"].append({"idx": st["idx"], "speaker": "teacher", "name": "师范生",
                        "text": text, "fias": f, "target": target, "behavior": ""})
    out["teacher"].append({"text": text, "fias": f, "target": target})

    if is_question(text):
        st["pending"] = True
        st["t0"] = time.time()
        out["need_yield"] = True
        return out

    # 非提问 → 其他学生可能抢答/走神
    out["students"] = _others(st, db, text, spoke=None)
    return out


class YieldReq(BaseModel):
    session: str
    wait_ms: int = 0


@app.post("/api/yield")
def api_yield(req: YieldReq) -> dict:
    """老师把话交出去 → 让学生回答（等待时长由前端传入）"""
    st = SESSIONS.get(req.session)
    if not st:
        raise HTTPException(404, "会话不存在")
    if not st["pending"]:
        raise HTTPException(400, "当前没有待回答的提问")
    db = store.Store(ROOT / "kejing_web.db")
    last_teacher = next((t for t in reversed(st["turns"]) if t["speaker"] == "teacher"), None)
    stu = pick_target((last_teacher or {}).get("text", ""), st["cast"],
                      (last_teacher or {}).get("target"), st["called"])
    st["called"].add(stu.name)
    out = stu.act((last_teacher or {}).get("text", ""), is_question=True, is_called=True)
    st["idx"] += 1
    db.add_turn(st["db_sid"], st["idx"], "student", stu.name, out["text"] or "（沉默）", 8,
                wait_ms=req.wait_ms, behavior=out["behavior"])
    st["turns"].append({"idx": st["idx"], "speaker": "student", "name": stu.name,
                        "text": out["text"] or "（沉默）", "fias": 8,
                        "wait_ms": req.wait_ms, "behavior": out["behavior"]})
    st["pending"] = False
    others = _others(st, db, (last_teacher or {}).get("text", ""), spoke=stu.name)
    return {"student": {"name": stu.name, "text": out["text"], "behavior": out["behavior"],
                        "source": out["source"], "wait_ms": req.wait_ms},
            "students": others}


def _others(st: dict, db: store.Store, teacher_text: str, spoke: str | None) -> list[dict]:
    """未被点名的其他学生：只可能出现「抢答 / 走神」"""
    res = []
    for s in st["cast"]:
        if s.name == spoke:
            continue
        out = s.act(teacher_text, is_question=False, is_called=False)
        if out["text"] and out["behavior"] == "抢答":
            st["idx"] += 1
            db.add_turn(st["db_sid"], st["idx"], "student", s.name, out["text"], 9,
                        behavior="抢答")
            st["turns"].append({"idx": st["idx"], "speaker": "student", "name": s.name,
                                "text": out["text"], "fias": 9, "behavior": "抢答"})
            res.append({"name": s.name, "text": out["text"], "behavior": "抢答"})
        elif out["behavior"] == "走神":
            st["idx"] += 1
            db.add_turn(st["db_sid"], st["idx"], "student", s.name, "（走神中）", None,
                        behavior="走神")
            st["turns"].append({"idx": st["idx"], "speaker": "student", "name": s.name,
                                "text": "（走神中）", "fias": None, "behavior": "走神"})
            res.append({"name": s.name, "text": None, "behavior": "走神"})
    return res


@app.post("/api/finish")
def api_finish(req: dict) -> dict:
    sid = req.get("session")
    st = SESSIONS.get(sid)
    if not st:
        raise HTTPException(404, "会话不存在")
    roster = [s.name for s in st["cast"]]
    m = fias.compute_metrics(st["turns"], roster)
    store.Store(ROOT / "kejing_web.db").save_metrics(st["db_sid"], m)
    rep = evaluator.build_report(lesson_mod.demo_lesson(), m, st["turns"], roster)
    return {"metrics": m, "report": rep,
            "metrics_text": fias.format_metrics(m, "本轮结果"),
            "report_text": evaluator.format_report(rep)}


@app.get("/api/state/{sid}")
def api_state(sid: str) -> dict:
    st = SESSIONS.get(sid)
    if not st:
        raise HTTPException(404, "会话不存在")
    return {"turns": st["turns"], "pending": st["pending"],
            "states": [{"name": s.name, "state": s.state} for s in st["cast"]]}
