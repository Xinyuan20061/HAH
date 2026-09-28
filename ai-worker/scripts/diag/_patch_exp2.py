# -*- coding: utf-8 -*-
"""Stage-1 patch: append decision ledger read model to experiments.py (v2)."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\services\agent\experiments.py"

s = io.open(PATH, encoding="utf-8").read()

READ_MODEL = '''

def get_decision(db: Session, user_id: int, decision_id: str) -> dict | None:
    """Decision ledger read model (plan §5).

    Joins one decision_id across signal -> frozen proposal -> confirmed action
    -> progress -> review. Facts come only from real records or the frozen
    baseline/target/protocol; nothing is invented for display. Returns None
    when the decision does not belong to the user (caller maps to 404 so ids
    are not enumerable).
    """
    row = db.scalar(
        select(AgentMicroExperiment).where(
            AgentMicroExperiment.decision_id == decision_id,
            AgentMicroExperiment.user_id == user_id,
        )
    )
    if row is None:
        return None
    baseline = _json(row.baseline_json, {})
    target = _json(row.target_json, {})
    protocol = _json(row.protocol_json, {})
    today = business_today()
    start = date.fromisoformat(row.start_date)
    end = date.fromisoformat(row.end_date)
    current = _observation(db, row.user_id, row.primary_metric, start, min(today, end))
    progress = _progress(current, target)
    outcome = _json(row.outcome_json, {})

    facts = []
    if isinstance(baseline, dict) and baseline.get("sample_size", 0) > 0:
        facts.append(
            {
                "name": f"{row.primary_metric}_baseline",
                "value": baseline.get("value"),
                "sample_size": baseline.get("sample_size", 0),
                "unit": baseline.get("unit", ""),
                "source": "confirmed_records",
            }
        )
    if current.get("sample_size", 0) > 0:
        facts.append(
            {
                "name": f"{row.primary_metric}_observed",
                "value": current.get("value"),
                "sample_size": current.get("sample_size", 0),
                "unit": current.get("unit", ""),
                "source": "confirmed_records",
            }
        )

    limitations = ["这是单个用户短周期的自我观察，不是随机对照试验，不能证明因果。"]
    if outcome.get("conclusion") == "insufficient_data" or current.get("sample_size", 0) == 0:
        limitations.append("实验期记录不足，系统不能据此给出正向结论（不把缺失当作零）。")

    events = db.scalars(
        select(HealthTimelineEvent)
        .where(
            HealthTimelineEvent.user_id == user_id,
            HealthTimelineEvent.ref_type == "agent_micro_experiment",
            HealthTimelineEvent.ref_id == row.id,
        )
        .order_by(HealthTimelineEvent.occurred_at, HealthTimelineEvent.id)
    ).all()
    timeline = [
        {"event_type": event.event_type, "occurred_at": utc_iso(event.occurred_at)}
        for event in events
    ]
    audits = db.scalars(
        select(AgentActionAudit)
        .where(
            AgentActionAudit.user_id == user_id,
            AgentActionAudit.action_key.like("experiment.%"),
        )
        .order_by(AgentActionAudit.id)
    ).all()
    for audit in audits:
        try:
            audit_input = json.loads(audit.input_json or "{}")
        except (TypeError, ValueError):
            audit_input = {}
        linked = str(audit_input.get("experiment_id")) == str(row.id) or (
            audit_input.get("decision_id") == row.decision_id
        )
        if linked:
            timeline.append(
                {
                    "event_type": f"audit:{audit.action_key}",
                    "status": audit.status,
                    "occurred_at": utc_iso(audit.created_at),
                }
            )

    proposal = build_experiment_proposal(row.insight_code)
    review = None
    if outcome:
        review = {
            "conclusion": outcome.get("conclusion"),
            "summary": outcome.get("summary"),
            "delta": outcome.get("delta"),
            "target_met": outcome.get("target_met"),
            "finalized_at": outcome.get("finalized_at"),
            "attribution": outcome.get("attribution", ""),
        }
    return {
        "decision_id": row.decision_id,
        "insight_code": row.insight_code,
        "signal": {
            "code": row.insight_code,
            "title": row.title,
            "observed_window": f"{row.start_date}..{row.end_date}",
            "source": "confirmed_records + goals",
        },
        "evidence": {
            "facts": facts,
            "data_coverage": {
                "observed_days": current.get("sample_size", 0) or baseline.get("sample_size", 0),
                "expected_days": int(protocol.get("days", 0)) or 0,
            },
            "knowledge_ids": [],
            "limitations": limitations,
            "boundary": "记录类提醒没有引用外部知识条目；建议按一般生活方式提示呈现。",
        },
        "proposal": {
            "version": proposal.get("version") if proposal else EXPERIMENT_VERSION,
            "title": row.title,
            "hypothesis": row.hypothesis,
            "primary_metric": row.primary_metric,
            "variants": (proposal or {}).get("variants", []),
            "chosen_variant": row.variant,
            "requires_confirmation": True,
            "stop_condition": protocol.get("stop_condition", ""),
            "protocol": protocol,
            "target": target,
        },
        "progress": {
            "status": row.status,
            "display_status": "ready_to_review" if (row.status == "active" and today >= end) else row.status,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "current": current,
            "target": target,
            "progress_pct": progress.get("progress_pct", 0),
            "target_met": progress.get("target_met", False),
        },
        "outcome": review,
        "timeline": timeline,
        "created_at": utc_iso(row.created_at),
        "completed_at": utc_iso(row.completed_at),
        "policy": "decision_id 由服务端生成并校验归属；结果只描述相关变化，不证明因果。",
    }
'''

if "def get_decision" in s:
    print("SKIP already present")
else:
    s = s.rstrip() + "\n" + READ_MODEL
    io.open(PATH, "w", encoding="utf-8", newline="").write(s)
    print("OK get_decision appended")
