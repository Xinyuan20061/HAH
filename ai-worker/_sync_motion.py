# -*- coding: utf-8 -*-
"""Sync patched motion.py to project mirror."""
from pathlib import Path

src = Path(r"C:\HealthMate\ai-worker\healthmate_worker\processors\motion.py")
dst = Path(r"D:\学习资料\计算机应用大赛\health-assistant\ai-worker\healthmate_worker\processors\motion.py")
dst.write_bytes(src.read_bytes())
print("SYNCED")
