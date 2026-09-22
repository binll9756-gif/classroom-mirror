# -*- coding: utf-8 -*-
"""启动课镜 Web 服务

    python run_web.py
    然后浏览器打开 http://127.0.0.1:8000

部署到公网时把 HOST 换成 0.0.0.0（这样别的机器才能访问）：
    python run_web.py --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse

import uvicorn

from kj.web import app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只允许本机访问；要别人能访问就填 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print(f"课镜 Web 服务启动中： http://{a.host}:{a.port}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
