"""Agent v4: consented N-of-1 micro experiments over audited health records.

The module deliberately separates three claims:
1. proposals are deterministic decision support, not model-generated treatment;
2. starting/finishing/cancelling always goes through the Action Registry;
3. outcome text describes an observed association and never causal proof.
"""

from __future__ import annotations

import json
import secrets
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import business_today, utc_day_bounds, utc_iso, utc_now
from app.models import AgentActionAudit, AgentMicroExperiment, HealthTimelineEvent, MotionScore
from app.services.agent.actions import execute_action
from app.services.health_data import daily_facts
from app.services.timeline import add_event


EXPERIMENT_VERSION = "v4.0"
ACTIVE_STATUSES = {"active"}


_DEFINITIONS = {
    "record_gap": {
        "title": "3–5 天最小记录实验",
        "hypothesis": "降低记录负担后，你更容易形成连续记录，从而让后续建议有更充分的数据依据。",
        "metric": "recorded_days",
        "measurement": "实验期内至少包含一项真实健康记录的天数",
        "stop": "记录让你感到压力或不适时可随时停止；不要求补录或编造数据。",
        "variants": {
            "gentle": {"label": "温和版", "days": 3, "goal": 2, "action": "每天只记录睡眠或饮水中的一项。"},
            "standard": {"label": "标准版", "days": 5, "goal": 4, "action": "每天记录睡眠、饮水或运动中的至少一项。"},
        },
    },
    "exercise_stall": {
        "title": "运动节奏重启实验",
        "hypothesis": "把单次门槛降到 10–15 分钟，可能比一次补足计划更容易恢复运动节奏。",
        "metric": "exercise_days",
        "measurement": "实验期内有真实运动记录的天数",
        "stop": "出现胸痛、晕厥、明显呼吸困难或持续疼痛时立即停止并寻求专业帮助。",
        "variants": {
            "gentle": {"label": "温和版", "days": 3, "goal": 2, "action": "任选 2 天完成 10 分钟轻量活动，以无痛和可交谈强度为准。"},
            "standard": {"label": "标准版", "days": 5, "goal": 3, "action": "任选 3 天完成 15 分钟轻到中等强度活动，中间至少安排 1 天恢复。"},
        },
    },
    "sleep_deficit": {
        "title": "睡眠恢复微实验",
        "hypothesis": "连续执行固定的睡前减负动作，可能与平均睡眠时长改善相关。",
        "metric": "sleep_average",
        "measurement": "实验期有记录日期的平均睡眠时长",
        "stop": "若持续严重失眠、白天功能明显受损或伴随其他不适，请停止自我实验并咨询专业人员。",
        "variants": {
            "gentle": {"label": "温和版", "days": 3, "goal": 0.3, "action": "睡前 30 分钟减少屏幕刺激，并尽量固定上床时间。"},
            "standard": {"label": "标准版", "days": 5, "goal": 0.5, "action": "固定起床时间，睡前 45 分钟减少屏幕刺激，并记录当天睡眠。"},
        },
    },
    "weight_rise": {
        "title": "体重测量一致性实验",
        "hypothesis": "统一测量条件并连续记录，可能帮助区分真实趋势与单次波动。",
        "metric": "weight_measurements",
        "measurement": "实验期内在相近条件下完成体重记录的天数",
        "stop": "若称重引发明显焦虑或不健康的饮食行为，请立即停止；本实验不以短期减重为目标。",
        "variants": {
            "gentle": {"label": "温和版", "days": 3, "goal": 2, "action": "任选 2 天在相近时间、相近着装下记录体重。"},
            "standard": {"label": "标准版", "days": 7, "goal": 5, "action": "任选 5 天在起床后、相近条件下记录体重，不根据单次结果极端节食。"},
        },
    },
    "motion_decline": {
        "title": "动作质量复核实验",
        "hypothesis": "主动降量并复核机位与技术，可能与下一阶段动作质量分改善相关。",
        "metric": "motion_average",
        "measurement": "实验期新产生的动作分析总体分均值",
        "stop": "出现疼痛、麻木、眩晕或动作无法稳定控制时立即停止，不为追分强行完成动作。",
        "variants": {
            "gentle": {"label": "温和版", "days": 3, "goal": 3, "action": "降低次数并完成 1 次同动作复测，优先检查机位和动作范围。"},
            "standard": {"label": "标准版", "days": 5, "goal": 5, "action": "降低训练量，在充分恢复后完成 2 次同动作复测。"},
        },
    },
}


def _json(value: str, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def build_experiment_proposal(insight_code: str) -> dict | None:
    definition = _DEFINITIONS.get(insight_code)
    if not definition:
        return None
    variants = []
    for key, item in definition["variants"].items():
        variants.append(
            {
                "key": key,
                "label": item["label"],
                "days": item["days"],
                "goal": item["goal"],
                "action": item["action"],
            }
        )
    return {
        "version": EXPERIMENT_VERSION,
        "insight_code": insight_code,
        "title": definition["title"],
        "hypothesis": definition["hypothesis"],
        "primary_metric": definition["metric"],
        "measurement": definition["measurement"],
        "stop_condition": definition["stop"],
        "variants": variants,
        "boundary": "这是个人自我观察，不是诊断或治疗；结果只能描述相关变化，不能证明因果。",
    }


def _observation(
    db: Session, user_id: int, metric: str, start: date, end: date
) -> dict:
    if end < start:
        return {"value": None, "sample_size": 0, "unit": "", "period_start": start.isoformat(), "period_end": end.isoformat()}
    if metric == "motion_average":
        start_dt = utc_day_bounds(start)[0]
        end_dt = utc_day_bounds(end)[1]
        scores = db.scalars(
            select(MotionScore).where(
                MotionScore.user_id == user_id,
                MotionScore.created_at.between(start_dt, end_dt),
            )
        ).all()
        values = [float(row.overall) for row in scores]
        value = round(sum(values) / len(values), 1) if values else None
        return {"value": value, "sample_size": len(values), "unit": "分", "period_start": start.isoformat(), "period_end": end.isoformat()}

    rows = daily_facts(db, user_id, start, end)
    if metric == "recorded_days":
        value = sum(1 for row in rows if any((row.get("observed") or {}).values()))
        sample_size, unit = value, "天"
    elif metric == "exercise_days":
        value = sum(1 for row in rows if (row.get("observed") or {}).get("exercise"))
        sample_size, unit = value, "天"
    elif metric == "weight_measurements":
        value = sum(1 for row in rows if row.get("weight_kg") is not None)
        sample_size, unit = value, "天"
    elif metric == "sleep_average":
        values = [float(row["sleep_hours"]) for row in rows if row.get("sleep_hours") is not None]
        value = round(sum(values) / len(values), 2) if values else None
        sample_size, unit = len(values), "小时"
    else:
        value, sample_size, unit = None, 0, ""
    return {"value": value, "sample_size": sample_size, "unit": unit, "period_start": start.isoformat(), "period_end": end.isoformat()}


def _target(definition: dict, variant: dict, baseline: dict) -> dict:
    metric = definition["metric"]
    goal = variant["goal"]
    if metric in {"sleep_average", "motion_average"}:
        base = baseline.get("value")
        return {
            "mode": "delta",
            "delta": goal,
            "value": round(base + goal, 2) if isinstance(base, (int, float)) else None,
            "unit": baseline.get("unit", ""),
            "requires_baseline": True,
        }
    return {"mode": "threshold", "value": goal, "unit": "天", "requires_baseline": False}


def _progress(current: dict, target: dict) -> dict:
    value = current.get("value")
    target_value = target.get("value")
    comparable = isinstance(value, (int, float)) and isinstance(target_value, (int, float))
    ratio = min(100, round(value / target_value * 100)) if comparable and target_value > 0 else 0
    return {
        "value": value,
        "sample_size": current.get("sample_size", 0),
        "unit": current.get("unit", target.get("unit", "")),
        "target_value": target_value,
        "target_unit": target.get("unit", ""),
        "progress_pct": ratio,
        "target_met": bool(comparable and value >= target_value),
    }


def serialize_experiment(db: Session, row: AgentMicroExperiment) -> dict:
    today = business_today()
    start = date.fromisoformat(row.start_date)
    end = date.fromisoformat(row.end_date)
    target = _json(row.target_json, {})
    baseline = _json(row.baseline_json, {})
    protocol = _json(row.protocol_json, {})
    current = _observation(db, row.user_id, row.primary_metric, start, min(today, end))
    progress = _progress(current, target)
    eligible = row.status == "active" and today >= end
    days_remaining = max(0, (end - today).days)
    return {
        "id": row.id,
        "decision_id": row.decision_id,
        "version": EXPERIMENT_VERSION,
        "insight_code": row.insight_code,
        "variant": row.variant,
        "title": row.title,
        "hypothesis": row.hypothesis,
        "primary_metric": row.primary_metric,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "status": row.status,
        "display_status": "ready_to_review" if eligible else row.status,
        "days_remaining": days_remaining,
        "baseline": baseline,
        "target": target,
        "protocol": protocol,
        "progress": progress,
        "outcome": _json(row.outcome_json, {}),
        "can_finish": eligible,
        "created_at": utc_iso(row.created_at),
        "completed_at": utc_iso(row.completed_at),
        "boundary": "进度来自真实健康记录；结果仅表示实验期内的相关变化，不证明因果。",
    }


def list_experiments(db: Session, user_id: int, limit: int = 10) -> list[dict]:
    rows = db.scalars(
        select(AgentMicroExperiment)
        .where(AgentMicroExperiment.user_id == user_id)
        .order_by(AgentMicroExperiment.created_at.desc(), AgentMicroExperiment.id.desc())
        .limit(limit)
    ).all()
    return [serialize_experiment(db, row) for row in rows]


def active_experiment(db: Session, user_id: int) -> AgentMicroExperiment | None:
    return db.scalar(
        select(AgentMicroExperiment)
        .where(
            AgentMicroExperiment.user_id == user_id,
            AgentMicroExperiment.status == "active",
        )
        .order_by(AgentMicroExperiment.id.desc())
    )


def variant_history(db: Session, user_id: int, insight_code: str) -> dict:
    """Past confirmed choices for one signal type, for explainable ordering.

    The history only surfaces as a reference label for the next proposal; it
    never overrides safety rules, stop conditions or the medical boundary.
    """
    rows = db.scalars(
        select(AgentMicroExperiment)
        .where(
            AgentMicroExperiment.user_id == user_id,
            AgentMicroExperiment.insight_code == insight_code,
        )
        .order_by(AgentMicroExperiment.created_at.desc(), AgentMicroExperiment.id.desc())
        .limit(20)
    ).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.variant] = counts.get(row.variant, 0) + 1
    preferred = max(counts, key=counts.get) if counts else None
    return {
        "counts": counts,
        "preferred": preferred,
        "total": sum(counts.values()),
        "policy": "历史选择仅用于方案排序与默认提示，不改变安全规则与医学边界。",
    }


def start_experiment(db: Session, user_id: int, insight_code: str, variant_key: str) -> dict:
    definition = _DEFINITIONS.get(insight_code)
    variant = definition and definition["variants"].get(variant_key)
    if not definition or not variant:
        raise ValueError("invalid_experiment")
    existing = active_experiment(db, user_id)
    if existing:
        raise RuntimeError("active_experiment_exists")
    today = business_today()
    end = today + timedelta(days=variant["days"] - 1)
    baseline_start = today - timedelta(days=variant["days"])
    baseline_end = today - timedelta(days=1)
    baseline = _observation(db, user_id, definition["metric"], baseline_start, baseline_end)
    target = _target(definition, variant, baseline)
    protocol = {
        "label": variant["label"],
        "days": variant["days"],
        "daily_action": variant["action"],
        "measurement": definition["measurement"],
        "stop_condition": definition["stop"],
    }

    decision_id = "dec-" + secrets.token_hex(8)

    def perform():
        row = AgentMicroExperiment(
            user_id=user_id,
            decision_id=decision_id,
            insight_code=insight_code,
            variant=variant_key,
            title=definition["title"],
            hypothesis=definition["hypothesis"],
            primary_metric=definition["metric"],
            start_date=today.isoformat(),
            end_date=end.isoformat(),
            status="active",
            baseline_json=json.dumps(baseline, ensure_ascii=False),
            target_json=json.dumps(target, ensure_ascii=False),
            protocol_json=json.dumps(protocol, ensure_ascii=False),
            outcome_json="{}",
        )
        db.add(row)
        db.flush()
        add_event(db, user_id, "agent_experiment_started", {"experiment_id": row.id, "decision_id": decision_id, "insight_code": insight_code, "variant": variant_key}, source="agent", ref_type="agent_micro_experiment", ref_id=row.id)
        return {"experiment_id": row.id}

    action = execute_action(
        db,
        user_id,
        "experiment.start",
        perform,
        confirmed=True,
        source="user",
        input_data={"insight_code": insight_code, "variant": variant_key, "decision_id": decision_id, "version": EXPERIMENT_VERSION},
    )
    row = db.get(AgentMicroExperiment, action["result"]["experiment_id"])
    return {"experiment": serialize_experiment(db, row), "action_audit_id": action["audit_id"]}


def finish_experiment(db: Session, user_id: int, experiment_id: int) -> dict | None:
    row = db.get(AgentMicroExperiment, experiment_id)
    if not row or row.user_id != user_id:
        return None
    if row.status != "active":
        return {"experiment": serialize_experiment(db, row), "already_finished": True}
    today = business_today()
    end = date.fromisoformat(row.end_date)
    if today < end:
        raise RuntimeError("experiment_not_ready")
    baseline = _json(row.baseline_json, {})
    target = _json(row.target_json, {})
    current = _observation(db, user_id, row.primary_metric, date.fromisoformat(row.start_date), end)
    progress = _progress(current, target)
    enough_data = current.get("sample_size", 0) > 0 and (
        not target.get("requires_baseline") or baseline.get("sample_size", 0) > 0
    )
    if not enough_data:
        conclusion = "insufficient_data"
        summary = "记录不足，暂时不能判断假设是否得到支持。"
    elif progress["target_met"]:
        conclusion = "supports_hypothesis"
        summary = "实验期指标达到预设目标，观察结果支持继续保持这一做法。"
    else:
        conclusion = "not_supported_yet"
        summary = "实验期指标尚未达到预设目标；这不等于做法无效，可结合执行情况再判断。"
    delta = None
    if isinstance(current.get("value"), (int, float)) and isinstance(baseline.get("value"), (int, float)):
        delta = round(current["value"] - baseline["value"], 2)
    outcome = {
        "conclusion": conclusion,
        "summary": summary,
        "baseline": baseline,
        "followup": current,
        "delta": delta,
        "target_met": progress["target_met"],
        "attribution": "这是单个用户短周期自我观察中的相关变化，不是随机对照试验，不能证明因果。",
        "finalized_at": utc_iso(utc_now()),
    }

    def perform():
        row.status = "completed"
        row.outcome_json = json.dumps(outcome, ensure_ascii=False)
        row.completed_at = utc_now()
        db.add(row)
        add_event(db, user_id, "agent_experiment_completed", {"experiment_id": row.id, "conclusion": conclusion}, source="agent", ref_type="agent_micro_experiment", ref_id=row.id)
        db.flush()
        return {"experiment_id": row.id, "conclusion": conclusion}

    action = execute_action(db, user_id, "experiment.finish", perform, confirmed=True, source="user", input_data={"experiment_id": row.id})
    return {"experiment": serialize_experiment(db, row), "action_audit_id": action["audit_id"], "already_finished": False}


def cancel_experiment(db: Session, user_id: int, experiment_id: int) -> dict | None:
    row = db.get(AgentMicroExperiment, experiment_id)
    if not row or row.user_id != user_id:
        return None
    if row.status != "active":
        return {"experiment": serialize_experiment(db, row), "already_closed": True}

    def perform():
        row.status = "cancelled"
        row.completed_at = utc_now()
        row.outcome_json = json.dumps({"conclusion": "cancelled_by_user", "attribution": "用户主动停止；已有健康记录保持不变。"}, ensure_ascii=False)
        db.add(row)
        add_event(db, user_id, "agent_experiment_cancelled", {"experiment_id": row.id}, source="agent", ref_type="agent_micro_experiment", ref_id=row.id)
        db.flush()
        return {"experiment_id": row.id}

    action = execute_action(db, user_id, "experiment.cancel", perform, confirmed=True, source="user", input_data={"experiment_id": row.id})
    return {"experiment": serialize_experiment(db, row), "action_audit_id": action["audit_id"], "already_closed": False}


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
            "evidence_type": "record_observation",
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
