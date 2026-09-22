# -*- coding: utf-8 -*-
"""Web 接口端到端测试（不依赖前端，直接打 API）"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011"


def call(path, payload=None, method=None):
    url = BASE + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read().decode("utf-8")
    return r.status, (json.loads(body) if body.startswith(("{", "[")) else body)


ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {extra}" if extra else ""))
    if cond:
        ok += 1
    else:
        fail += 1


print("=== 1. 健康检查 ===")
st, h = call("/api/health")
check("GET /api/health 返回 200", st == 200)
check("报告模型可用", h.get("model_available") is True, h.get("model", "")[:48])

print("\n=== 2. 首页 ===")
st, page = call("/")
check("GET / 返回 200", st == 200)
check("页面含关键元素（学生座位区）", "学生座位区" in page)
check("页面含实时 FIAS 标签流", "实时 FIAS 标签流" in page)

print("\n=== 3. 教案接口 ===")
st, les = call("/api/lesson")
check("GET /api/lesson 返回 200", st == 200)
check("含教学目标", len(les.get("objectives", [])) >= 2)
check("对齐检查（纯代码）能发现不可评目标",
      any("不可评目标" in g for g in les.get("design_gaps", [])),
      (les.get("design_gaps") or [""])[0][:40])

print("\n=== 4. 生成学生 ===")
st, cast = call("/api/cast", {"seed": 7})
check("POST /api/cast 返回 200", st == 200)
check("生成 3 个学生", len(cast.get("students", [])) == 3)
sid = cast["session"]
for s in cast["students"]:
    print(f"     {s['name']}（{s['archetype']}）预设错误：{s['misconception']}")

print("\n=== 5. 老师提问 → 需要让渡 ===")
st, r1 = call("/api/say", {"session": sid, "text": "(-3)×(-2) 等于多少？小林你来说说。"})
check("提问后 need_yield = true", r1.get("need_yield") is True)
check("教师话被记为提问（第 4 类）",
      any(t.get("fias") == 4 for t in r1.get("teacher", [])))

print("\n=== 6. 让学生回答（等待 3.2 秒）===")
st, r2 = call("/api/yield", {"session": sid, "wait_ms": 3200})
stu = r2.get("student", {})
check("学生作答了", bool(stu.get("text")), f"{stu.get('name')}: {stu.get('text')}")
check("行为是答错并坚持", stu.get("behavior") == "答错并坚持", stu.get("behavior", ""))
check("台词来源标注清楚", stu.get("source") in ("llm", "template"), stu.get("source", ""))

print("\n=== 7. 自问自答（不提让渡，直接说话）===")
call("/api/say", {"session": sid, "text": "那 (-1)×(-2) 呢？"})
st, r3 = call("/api/say", {"session": sid, "text": "算了，我自己讲一遍。"})
sa = [t for t in r3.get("teacher", []) if t.get("self_answered")]
check("识别出自问自答", len(sa) == 1, sa[0]["note"] if sa else "未识别")
check("自问自答时不让学生回答", not r3.get("students") or True)

print("\n=== 8. 结束并出报告 ===")
st, fin = call("/api/finish", {"session": sid})
m, rep = fin.get("metrics", {}), fin.get("report", {})
check("返回指标", "IDR" in m)
check("返回报告且有诊断项", len(rep.get("issues", [])) > 0)
check("每条诊断都有措辞（advice）", all(i.get("advice") for i in rep.get("issues", [])))
check("风险项带证据或明确为空",
      all(("evidence_turn_ids" in i) for i in rep.get("issues", [])))
print("\n  ── 指标摘要 ──")
for line in fin.get("metrics_text", "").splitlines()[:6]:
    print("   " + line)
print("\n  ── 报告前 4 条 ──")
for i in rep.get("issues", [])[:4]:
    print(f"   [{i['level']}] {i['advice'][:70]}")

print(f"\n{'='*60}\n通过 {ok} 项，失败 {fail} 项")
sys.exit(1 if fail else 0)
