# -*- coding: utf-8 -*-
"""Byte-copy patched worker files to the project directory mirror."""
import io
from pathlib import Path

pairs = [
    (r"C:\HealthMate\ai-worker\healthmate_worker\processors\motion_review.py",
     r"D:\学习资料\计算机应用大赛\health-assistant\ai-worker\healthmate_worker\processors\motion_review.py"),
    (r"C:\HealthMate\ai-worker\healthmate_worker\processors\motion.py",
     r"D:\学习资料\计算机应用大赛\health-assistant\ai-worker\healthmate_worker\processors\motion.py"),
    (r"C:\HealthMate\ai-worker\healthmate_worker\visualize.py",
     r"D:\学习资料\计算机应用大赛\health-assistant\ai-worker\healthmate_worker\visualize.py"),
    (r"C:\HealthMate\ai-worker\tests\test_motion_review.py",
     r"D:\学习资料\计算机应用大赛\health-assistant\ai-worker\tests\test_motion_review.py"),
]
for src, dst in pairs:
    data = Path(src).read_bytes()
    Path(dst).write_bytes(data)
    print("SYNCED", dst)
