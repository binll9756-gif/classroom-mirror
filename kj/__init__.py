# -*- coding: utf-8 -*-
"""课镜 · 核心模块

  fias.py       ★纯代码：FIAS 九类 + 编码 + 指标计算（不调用模型）
  students.py   ★虚拟学生：画像 + 认知状态机 + 台词（代码决定行为，模型只说话）
  lesson.py     教案解析 + 目标—活动—评价对齐检查（对齐检查是纯代码）
  evaluator.py  ★评价报告：代码发现问题（带证据），模型只负责措辞
  store.py      事件流存储（SQLite）+ 两轮对比
  llm.py        模型调用层（本地 Ollama / OpenAI 兼容云端，自动降级）
"""
__all__ = ["fias", "students", "lesson", "evaluator", "store", "llm"]
