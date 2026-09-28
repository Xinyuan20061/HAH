# -*- coding: utf-8 -*-
"""Stage-1 patch: experiments.py gains decision_id + decision ledger read model."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\services\agent\experiments.py"

s = io.open(PATH, encoding="utf-8").read()

# 1) import secrets
OLD_IMPORT = "import json\nfrom datetime import date, timedelta\n"
NEW_IMPORT = "import json\nimport secrets\nfrom datetime import date, timedelta\n"
assert s.count(OLD_IMPORT) == 1
s = s.replace(OLD_IMPORT, NEW_IMPORT)

# 2) serialize_experiment exposes decision_id
OLD_SER = '''        "id": row.id,
        "version": EXPERIMENT_VERSION,
        "insight_code": row.insight_code,'''
NEW_SER = '''        "id": row.id,
        "decision_id": row.decision_id,
        "version": EXPERIMENT_VERSION,
        "insight_code": row.insight_code,'''
assert s.count(OLD_SER) == 1
s = s.replace(OLD_SER, NEW_SER)

# 3) start_experiment writes decision_id into row + audit input + timeline payload
OLD_START_ROW = '''    def perform():
        row = AgentMicroExperiment(
            user_id=user_id,
            insight_code=insight_code,
            variant=variant_key,'''
NEW_START_ROW = '''    decision_id = "dec-" + secrets.token_hex(8)

    def perform():
        row = AgentMicroExperiment(
            user_id=user_id,
            decision_id=decision_id,
            insight_code=insight_code,
            variant=variant_key,'''
assert s.count(OLD_START_ROW) == 1
s = s.replace(OLD_START_ROW, NEW_START_ROW)

OLD_START_EVENT = '''        add_event(db, user_id, "agent_experiment_started", {"experiment_id": row.id, "insight_code": insight_code, "variant": variant_key}, source="agent", ref_type="agent_micro_experiment", ref_id=row.id)
        return {"experiment_id": row.id}'''
NEW_START_EVENT = '''        add_event(db, user_id, "agent_experiment_started", {"experiment_id": row.id, "decision_id": decision_id, "insight_code": insight_code, "variant": variant_key}, source="agent", ref_type="agent_micro_experiment", ref_id=row.id)
        return {"experiment_id": row.id}'''
assert s.count(OLD_START_EVENT) == 1
s = s.replace(OLD_START_EVENT, NEW_START_EVENT)

OLD_START_AUDIT = '''        input_data={"insight_code": insight_code, "variant": variant_key, "version": EXPERIMENT_VERSION},'''
NEW_START_AUDIT = '''        input_data={"insight_code": insight_code, "variant": variant_key, "decision_id": decision_id, "version": EXPERIMENT_VERSION},'''
assert s.count(OLD_START_AUDIT) == 1
s = s.replace(OLD_START_AUDIT, NEW_START_AUDIT)

io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK experiments.py patched (decision_id wiring)")
