# -*- coding: utf-8 -*-
"""Add evidence_type to get_decision evidence block."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\services\agent\experiments.py"

s = io.open(PATH, encoding="utf-8").read()

OLD = '''            "knowledge_ids": [],
            "limitations": limitations,
            "boundary": "记录类提醒没有引用外部知识条目；建议按一般生活方式提示呈现。",'''
NEW = '''            "knowledge_ids": [],
            "limitations": limitations,
            "evidence_type": "record_observation",
            "boundary": "记录类提醒没有引用外部知识条目；建议按一般生活方式提示呈现。",'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)
io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK evidence_type added")
