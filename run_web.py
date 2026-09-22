# -*- coding: utf-8 -*-
"""启动课镜 Web 服务

    python run_web.py
    然后浏览器打开 http://127.0.0.1:8000

部署到公网时把 HOST 换成 0.0.0.0（这样别的机器才能访问）：
    python run_web.py --host 0.0.0.0 --port 8000

也可以直接双击项目里的「① 启动网页版.bat」。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ★ 保证中文能在控制台正常显示（.bat 里已 chcp 65001，这里再把 Python 输出也设成 UTF-8）
#   line_buffering=True 很重要：不加的话输出被块缓冲，重定向/双击时横幅一直不显示
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)   # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)   # type: ignore[attr-defined]
except Exception:
    pass

import uvicorn  # noqa: E402

from kj import llm  # noqa: E402
from kj.web import app  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只允许本机访问；要别人能访问就填 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    print("=" * 60)
    print("  课镜 · AI 微格教学教练（网页版）")
    print("=" * 60)
    print(f"  模型：{llm.status()}")
    if not llm.available():
        print("  ⚠️ 模型不可用 —— 学生台词会用模板兜底（FIAS 指标仍真实计算）")
        print("     解决：确认 Ollama 已启动（任务栏应该有它的图标）")
    print()
    print(f"  ✅ 请用浏览器打开：  http://127.0.0.1:{a.port}")
    print("  ✅ 停止服务：在本窗口按 Ctrl+C，或直接关掉这个窗口")
    print("=" * 60)
    print()
    try:
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    except KeyboardInterrupt:
        print("\n服务已停止。")
