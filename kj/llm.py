# -*- coding: utf-8 -*-
"""模型调用薄封装：有 Key 走真模型，没 Key 自动降级为离线模式。

新手最常问的两个问题在这里回答：
  1) 用哪家模型？—— 任何"OpenAI 兼容"的都可以。国内常用：DeepSeek / 通义千问 / 智谱 GLM。
  2) Key 放哪？—— 放到环境变量，【不要写死在代码里】。
     在 PowerShell 里临时设置（关掉窗口就失效）：
        $env:OPENAI_API_KEY="sk-xxxx"
        $env:OPENAI_BASE_URL="https://api.deepseek.com/v1"
        $env:KJ_MODEL="deepseek-chat"
"""
from __future__ import annotations

import os

MODEL = os.getenv("KJ_MODEL", "gpt-4o-mini")
BASE_URL = os.getenv("OPENAI_BASE_URL") or None
API_KEY = os.getenv("OPENAI_API_KEY") or ""


def has_key() -> bool:
    return bool(API_KEY)


def chat(system: str, user: str, temperature: float = 0.8) -> str | None:
    """调用大模型。没有 Key 或调用失败时返回 None（由调用方走离线兜底）。"""
    if not has_key():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:                      # 网络/额度/模型名错误都不该让程序崩
        print(f"[LLM 调用失败，已降级为离线模式] {type(e).__name__}: {e}")
        return None


def status() -> str:
    if has_key():
        return f"在线模式：{MODEL} @ {BASE_URL or '默认 OpenAI 地址'}"
    return "离线模式（未检测到 OPENAI_API_KEY）—— 学生台词用模板生成，FIAS 指标照常真实计算"
