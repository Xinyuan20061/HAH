# -*- coding: utf-8 -*-
"""Stage-1 doc sync: migration head 0021 -> 0022, test counts 161/43."""
import io

TARGETS = [
    (r"D:\学习资料\计算机应用大赛\health-assistant\README.md", [
        ("0021_empty_default_nickname", "0022_agent_decision_id"),
        ("后端 SQLite 156 项、Worker 99 项、小程序 41 项", "后端 SQLite 161 项、Worker 99 项、小程序 43 项"),
    ]),
    (r"D:\学习资料\计算机应用大赛\health-assistant\docs\DELIVERY_CHECKLIST.md", [
        ("0021_empty_default_nickname", "0022_agent_decision_id"),
    ]),
    (r"D:\学习资料\计算机应用大赛\health-assistant\docs\AUTO_MOTION_RECOGNITION.md", [
        ("0021_empty_default_nickname", "0022_agent_decision_id"),
    ]),
    (r"D:\学习资料\计算机应用大赛\health-assistant\docs\WECHAT_CLOUD_RUN_DEPLOY.md", [
        ("0021_empty_default_nickname (head)", "0022_agent_decision_id (head)"),
    ]),
    (r"D:\学习资料\计算机应用大赛\health-assistant\docs\REVIEWER_AUDIT_AND_ROADMAP_2026-09-24.md", [
        ("0021_empty_default_nickname", "0022_agent_decision_id"),
    ]),
]

for path, pairs in TARGETS:
    s = io.open(path, encoding="utf-8").read()
    changed = []
    for old, new in pairs:
        n = s.count(old)
        if n == 0:
            print("MISS  %s :: %s" % (path, old[:40]))
            continue
        s = s.replace(old, new)
        changed.append((old[:24], n))
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("OK    %s %s" % (path, changed))
