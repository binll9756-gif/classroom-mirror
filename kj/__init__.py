# -*- coding: utf-8 -*-
"""课镜 · 最小可跑骨架

四个模块，各管一件事：
  fias.py      纯代码 —— FIAS 九类 + 指标计算（不调用模型）
  students.py  虚拟学生 —— 画像 + 认知状态机 + 台词
  store.py     事件流存储（SQLite）+ 两轮对比
  llm.py       模型调用薄封装（没 Key 自动降级为离线）
"""
__all__ = ["fias", "students", "store", "llm"]
