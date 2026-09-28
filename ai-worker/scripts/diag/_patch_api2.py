# -*- coding: utf-8 -*-
"""Stage-1 patch: experiment_timeline() in experiments.py + /insights assembly."""
import io

PATH_EXP = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\services\agent\experiments.py"
PATH_API = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\api\v1\agent.py"

# --- experiments.py: append experiment_timeline ---
s = io.open(PATH_EXP, encoding="utf-8").read()
TIMELINE_FN = '''

def experiment_timeline(
    db: Session, user_id: int, insight_code: str, limit: int = 5
) -> list[dict]:
    """Short action timeline for one signal type: what the user confirmed,
    whether it is running, and what the review concluded."""
    rows = db.scalars(
        select(AgentMicroExperiment)
        .where(
            AgentMicroExperiment.user_id == user_id,
            AgentMicroExperiment.insight_code == insight_code,
        )
        .order_by(AgentMicroExperiment.created_at.desc(), AgentMicroExperiment.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "decision_id": row.decision_id,
            "variant": row.variant,
            "status": row.status,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "outcome": _json(row.outcome_json, {}).get("conclusion"),
            "created_at": utc_iso(row.created_at),
        }
        for row in rows
    ]
'''
if "def experiment_timeline" not in s:
    s = s.rstrip() + "\n" + TIMELINE_FN
    io.open(PATH_EXP, "w", encoding="utf-8", newline="").write(s)
    print("OK experiment_timeline appended")
else:
    print("SKIP experiment_timeline exists")

# --- api/v1/agent.py: import + insights assembly ---
a = io.open(PATH_API, encoding="utf-8").read()

OLD_IMPORT = "    cancel_experiment,\n    get_decision,\n    variant_history,\n    EXPERIMENT_VERSION,\n)"
NEW_IMPORT = "    cancel_experiment,\n    get_decision,\n    experiment_timeline,\n    variant_history,\n    EXPERIMENT_VERSION,\n)"
assert a.count(OLD_IMPORT) == 1
a = a.replace(OLD_IMPORT, NEW_IMPORT)

OLD_HELPER = '''def _recent_insight_feedback(db: Session, user_id: int) -> dict:'''
NEW_HELPER = '''def _contract_evidence(item: dict, data_quality: dict) -> dict:
    """Map internal evidence_meta + global coverage into the §5 evidence shape.

    observed_days and expected_days are always both returned; missing records
    are never silently turned into zero coverage claims.
    """
    meta = item.get("evidence_meta") or {}
    coverage = data_quality or {}
    return {
        "facts": meta.get("facts", []),
        "data_coverage": {
            "observed_days": coverage.get("recorded_days", 0),
            "expected_days": coverage.get("expected_days", 7),
        },
        "knowledge_ids": meta.get("knowledge_ids", []),
        "limitations": meta.get("limitations", []),
        "evidence_type": meta.get("evidence_type", "general_guidance"),
    }


def _recent_insight_feedback(db: Session, user_id: int) -> dict:'''
assert a.count(OLD_HELPER) == 1
a = a.replace(OLD_HELPER, NEW_HELPER)

OLD_LOOP = '''    for item in insights.get("insights", []):
        item["user_feedback"] = recent_feedback.get(item.get("code"))
        item["experiment_proposal"] = build_experiment_proposal(item.get("code"))
        item["proposal_history"] = variant_history(db, user.id, item.get("code"))
        item["active_experiment"] = (
            current_payload if current_experiment and current_experiment.insight_code == item.get("code") else None
        )'''
NEW_LOOP = '''    for item in insights.get("insights", []):
        item["user_feedback"] = recent_feedback.get(item.get("code"))
        item["experiment_proposal"] = build_experiment_proposal(item.get("code"))
        item["proposal_history"] = variant_history(db, user.id, item.get("code"))
        item["evidence"] = _contract_evidence(item, insights.get("data_quality", {}))
        item["decision_id"] = (
            current_payload.get("decision_id")
            if current_experiment and current_experiment.insight_code == item.get("code") and current_payload
            else None
        )
        item["action_timeline"] = experiment_timeline(db, user.id, item.get("code"))
        item["active_experiment"] = (
            current_payload if current_experiment and current_experiment.insight_code == item.get("code") else None
        )'''
assert a.count(OLD_LOOP) == 1
a = a.replace(OLD_LOOP, NEW_LOOP)

io.open(PATH_API, "w", encoding="utf-8", newline="").write(a)
print("OK /insights assembly updated")
