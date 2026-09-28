# -*- coding: utf-8 -*-
"""Ensure experiments.py imports the models used by get_decision."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\services\agent\experiments.py"

s = io.open(PATH, encoding="utf-8").read()

OLD = "from app.models import AgentMicroExperiment, MotionScore\n"
NEW = "from app.models import AgentActionAudit, AgentMicroExperiment, HealthTimelineEvent, MotionScore\n"
if s.count(OLD) == 1:
    s = s.replace(OLD, NEW)
    io.open(PATH, "w", encoding="utf-8", newline="").write(s)
    print("OK imports updated")
elif NEW in s:
    print("SKIP already imported")
else:
    print("ERROR import line not found")
