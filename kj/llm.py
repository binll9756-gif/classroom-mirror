# -*- coding: utf-8 -*-
"""模型调用层：支持【本地 Ollama】与【任何 OpenAI 兼容云端接口】

★ 这里内置修好了两个我实测踩过的坑，队友不用再踩：

  坑 1：Qwen3 系列默认开"思考模式"。
        它会把 token 全花在推理上 —— 返回内容为空，而且慢 8 倍。
        实测：不关 think → 17.1 秒、内容为空；关了 → 1.9 秒、正常台词。
        → 所以走 Ollama 原生接口并显式传 think=false。

  坑 2：结构化输出要可靠。
        Ollama 用 format="json"；云端用 response_format={"type":"json_object"}。
        并且做「剥代码围栏 → 取首尾括号 → 宽松修复 → 再解析 → 失败重试一次」。

配置方式（写在项目根目录 .env 里，已被 .gitignore 挡住）：
    KJ_PROVIDER=ollama
    OPENAI_BASE_URL=http://localhost:11434
    KJ_MODEL=qwen3.5:9b
    OPENAI_API_KEY=ollama          # Ollama 不校验，随便填
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- 配置加载
def load_env(path: str | Path | None = None) -> None:
    """极简 .env 加载器（不引入额外依赖）。已存在的环境变量优先，不覆盖。

    默认读【仓库根目录】的 .env —— 这样不管从哪个目录运行都能读到。
    """
    p = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_env()

MODEL = os.getenv("KJ_MODEL", "qwen3.5:9b")
BASE_URL = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
API_KEY = os.getenv("OPENAI_API_KEY") or ""
TIMEOUT = int(os.getenv("KJ_TIMEOUT", "180"))


def _ollama_host() -> str:
    """Ollama 地址。

    ★★ 这里踩过一个很隐蔽的坑，实测数据：
        http://localhost:11434   /api/tags  平均 2.036s
        http://127.0.0.1:11434   /api/tags  平均 0.014s   ← 快 145 倍
        http://localhost:11434   /api/chat  平均 4.13s
        http://127.0.0.1:11434   /api/chat  平均 1.77s    ← 快 2.3 倍

       原因：Windows 会把 localhost 先解析成 IPv6 的 ::1，连不上再回退 IPv4，
             每次请求都白等约 2 秒。
       → 所以这里强制把 localhost 换成 127.0.0.1。
    """
    host = (os.getenv("OLLAMA_HOST") or "").strip()
    if not host:
        host = BASE_URL.replace("/v1", "") if "11434" in BASE_URL else "http://127.0.0.1:11434"
    return host.rstrip("/").replace("localhost", "127.0.0.1")


def provider() -> str:
    """判断用哪个通道。显式配置优先，其次看端口/关键词自动识别。"""
    p = (os.getenv("KJ_PROVIDER") or "").lower()
    if p in ("ollama", "openai"):
        return p
    if "11434" in BASE_URL or "ollama" in BASE_URL.lower():
        return "ollama"
    return "openai"


_avail_cache: list = [0.0, False]
_AVAIL_TTL = 5.0          # 秒：避免每次 chat 都去探一次服务


def available() -> bool:
    """有可用的模型通道吗？（结果缓存 5 秒，避免每个回合重复探测）"""
    now = time.time()
    if now - _avail_cache[0] < _AVAIL_TTL:
        return _avail_cache[1]
    if provider() == "ollama":
        try:
            with urllib.request.urlopen(f"{_ollama_host()}/api/tags", timeout=5):
                ok = True
        except Exception:
            ok = False
    else:
        ok = bool(API_KEY)
    _avail_cache[0], _avail_cache[1] = now, ok
    return ok


def status() -> str:
    if provider() == "ollama":
        ok = "在线" if available() else "连不上（Ollama 没启动？）"
        return f"本地 Ollama · {MODEL} · {ok}（数据不出本机）"
    if API_KEY:
        return f"云端 · {MODEL} @ {BASE_URL or 'OpenAI 默认地址'}"
    return "离线模式（无可用模型）—— 学生台词用模板，FIAS 指标照常真实计算"


# ---------------------------------------------------------------- 底层请求
def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_ollama(system: str, user: str, temperature: float,
                 json_mode: bool, max_tokens: int) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "think": False,                      # ★ 必传：否则 Qwen3 返回空内容
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if json_mode:
        payload["format"] = "json"           # ★ Ollama 可靠的 JSON 方式
    out = _post(f"{_ollama_host()}/api/chat", payload)
    msg = out.get("message") or {}
    return (msg.get("content") or "").strip()


def _call_openai(system: str, user: str, temperature: float,
                 json_mode: bool, max_tokens: int) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=API_KEY or "not-needed", base_url=BASE_URL or None)
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def chat(system: str, user: str, temperature: float = 0.8,
         json_mode: bool = False, max_tokens: int = 800,
         retries: int = 2) -> str | None:
    """调用模型。失败自动重试；彻底失败返回 None（调用方走兜底）。"""
    if not available():
        return None
    last = ""
    for attempt in range(retries + 1):
        try:
            if provider() == "ollama":
                text = _call_ollama(system, user, temperature, json_mode, max_tokens)
            else:
                text = _call_openai(system, user, temperature, json_mode, max_tokens)
            if text:
                return text
            last = "返回内容为空"
        except urllib.error.URLError as e:
            last = f"网络错误 {e}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(1.0 + attempt)
    print(f"[模型调用失败，已降级] {last}")
    return None


# ---------------------------------------------------------------- JSON 处理
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict | list | None:
    """尽力从模型输出里抠出 JSON：剥围栏 → 取首尾括号 → 宽松修复 → 解析。"""
    if not text:
        return None
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            cand = t[i:j + 1]
            variants = [
                cand,
                re.sub(r",(\s*[}\]])", r"\1", cand),
                re.sub(r"(?<![\\\w])'", '"', cand),
            ]
            for v in variants:
                try:
                    return json.loads(v)
                except Exception:
                    continue
    return None


def chat_json(system: str, user: str, temperature: float = 0.2,
              max_tokens: int = 1200, retries: int = 2) -> dict | list | None:
    """要结构化结果。解析失败会把错误回喂给模型再试一次。"""
    sys_json = system + "\n\n【输出要求】只输出一个合法的 JSON，不要任何解释文字、不要 Markdown 代码围栏。"
    text = chat(sys_json, user, temperature, json_mode=True,
                max_tokens=max_tokens, retries=retries)
    obj = extract_json(text or "")
    if obj is not None:
        return obj
    if text:
        fix = chat(sys_json,
                   f"下面这段不是合法 JSON，请修正后【只输出修正后的 JSON】：\n\n{text[:2000]}",
                   temperature=0.0, json_mode=True, max_tokens=max_tokens, retries=1)
        obj = extract_json(fix or "")
        if obj is not None:
            return obj
    return None
