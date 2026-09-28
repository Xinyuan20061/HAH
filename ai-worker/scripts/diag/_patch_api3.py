# -*- coding: utf-8 -*-
"""Rename assembled contract evidence key to evidence_contract (keep string evidence)."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\api\v1\agent.py"

s = io.open(PATH, encoding="utf-8").read()

OLD = '        item["evidence"] = _contract_evidence(item, insights.get("data_quality", {}))'
NEW = '        item["evidence_contract"] = _contract_evidence(item, insights.get("data_quality", {}))'
assert s.count(OLD) == 1
s = s.replace(OLD, NEW)
io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK evidence_contract key")
