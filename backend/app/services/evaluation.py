from __future__ import annotations
from app.core.time import utc_now, utc_iso
import json, math
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import (
    EvaluationEvent,
    EvaluationBenchmark,
    FoodAnalysisSession,
    AIJob,
    AgentActionAudit,
    AgentMicroExperiment,
    SafetyEvent,
    HealthPlanItem,
)


def record_metric(
    db: Session,
    user_id: int | None,
    name: str,
    value: float = 1,
    unit: str = "count",
    source: str = "runtime",
    success: bool = True,
    meta: dict | None = None,
    commit: bool = False,
):
    item = EvaluationEvent(
        user_id=user_id,
        metric_name=name,
        metric_value=float(value),
        unit=unit,
        source=source,
        success=success,
        meta_json=json.dumps(meta or {}, ensure_ascii=False, default=str),
        occurred_at=utc_now(),
    )
    db.add(item)
    if commit:
        db.commit()
    return item


def _percentile(values: list[float], p: float):
    if not values:
        return None
    s = sorted(values)
    idx = (len(s) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return round(s[lo], 2)
    return round(s[lo] * (hi - idx) + s[hi] * (idx - lo), 2)


def _rate(num: int, den: int):
    return round(num / den * 100, 1) if den else None


def dashboard(db: Session, user_id: int, days: int = 30):
    since = utc_now() - timedelta(days=days)
    events = db.scalars(
        select(EvaluationEvent).where(
            EvaluationEvent.user_id == user_id, EvaluationEvent.occurred_at >= since
        )
    ).all()
    by = {}
    for e in events:
        by.setdefault(e.metric_name, []).append(e)

    def lat(name):
        return [x.metric_value for x in by.get(name, []) if x.success]

    food = db.scalars(
        select(FoodAnalysisSession).where(
            FoodAnalysisSession.user_id == user_id,
            FoodAnalysisSession.created_at >= since,
        )
    ).all()
    motion = db.scalars(
        select(AIJob).where(
            AIJob.user_id == user_id,
            AIJob.job_type == "motion_pose",
            AIJob.created_at >= since,
        )
    ).all()
    actions = db.scalars(
        select(AgentActionAudit).where(
            AgentActionAudit.user_id == user_id, AgentActionAudit.created_at >= since
        )
    ).all()
    plan_items = db.scalars(
        select(HealthPlanItem).where(
            HealthPlanItem.user_id == user_id, HealthPlanItem.created_at >= since
        )
    ).all()
    safety = db.scalars(
        select(SafetyEvent).where(
            SafetyEvent.user_id == user_id, SafetyEvent.created_at >= since
        )
    ).all()
    # Agent v4 micro experiments: every row is created only after explicit user
    # confirmation, so table rows ARE the confirmed real samples (no preview rows).
    experiments = db.scalars(
        select(AgentMicroExperiment).where(
            AgentMicroExperiment.user_id == user_id,
            AgentMicroExperiment.created_at >= since,
        )
    ).all()
    experiment_status = {}
    for x in experiments:
        experiment_status[x.status] = experiment_status.get(x.status, 0) + 1
    experiment_outcomes = {}
    for x in experiments:
        if not x.outcome_json:
            continue
        try:
            meta = json.loads(x.outcome_json)
        except (TypeError, ValueError):
            continue
        conclusion = str(meta.get("conclusion") or "unknown")
        experiment_outcomes[conclusion] = experiment_outcomes.get(conclusion, 0) + 1
    benchmarks = db.scalars(
        select(EvaluationBenchmark)
        .where(EvaluationBenchmark.user_id == user_id)
        .order_by(EvaluationBenchmark.created_at.desc())
    ).all()
    seen = set()
    latest = []
    for b in benchmarks:
        k = (b.task_type, b.metric_name)
        if k in seen:
            continue
        seen.add(k)
        latest.append(
            {
                "task_type": b.task_type,
                "metric_name": b.metric_name,
                "value": b.value,
                "unit": b.unit,
                "sample_size": b.sample_size,
                "notes": b.notes,
                "dataset": b.dataset or "",
                "evidence_level": b.evidence_level or "",
                "retriever_version": b.retriever_version or "",
                "measured_at": utc_iso(b.created_at),
            }
        )
    finalized = sum(1 for x in food if x.finalized_record_id)
    corrected = sum(1 for x in food if x.correction_count > 0)
    motion_done = sum(1 for x in motion if x.status in {"completed", "done"})
    motion_failed = sum(1 for x in motion if x.status == "failed")
    action_executed = sum(1 for x in actions if x.status == "executed")
    action_blocked = sum(1 for x in actions if x.status == "blocked")
    runtime = [
        {
            "key": "food_analysis_success",
            "label": "识餐成功率",
            "value": _rate(
                sum(1 for x in by.get("food_analysis_latency_ms", []) if x.success),
                len(by.get("food_analysis_latency_ms", [])),
            ),
            "unit": "%",
            "sample_size": len(by.get("food_analysis_latency_ms", [])),
        },
        {
            "key": "food_correction_rate",
            "label": "识餐校正率",
            "value": _rate(corrected, len(food)),
            "unit": "%",
            "sample_size": len(food),
        },
        {
            "key": "food_finalize_rate",
            "label": "识餐入库率",
            "value": _rate(finalized, len(food)),
            "unit": "%",
            "sample_size": len(food),
        },
        {
            "key": "food_latency_p50",
            "label": "识餐响应 P50",
            "value": _percentile(lat("food_analysis_latency_ms"), 0.5),
            "unit": "ms",
            "sample_size": len(lat("food_analysis_latency_ms")),
        },
        {
            "key": "agent_latency_p50",
            "label": "Agent 响应 P50",
            "value": _percentile(lat("agent_response_latency_ms"), 0.5),
            "unit": "ms",
            "sample_size": len(lat("agent_response_latency_ms")),
        },
        {
            "key": "agent_latency_p95",
            "label": "Agent 响应 P95",
            "value": _percentile(lat("agent_response_latency_ms"), 0.95),
            "unit": "ms",
            "sample_size": len(lat("agent_response_latency_ms")),
        },
        {
            "key": "weekly_latency_p50",
            "label": "周报 AI P50",
            "value": _percentile(lat("weekly_summary_latency_ms"), 0.5),
            "unit": "ms",
            "sample_size": len(lat("weekly_summary_latency_ms")),
        },
        {
            "key": "motion_success",
            "label": "视频任务成功率",
            "value": _rate(motion_done, motion_done + motion_failed),
            "unit": "%",
            "sample_size": motion_done + motion_failed,
        },
        {
            "key": "motion_latency_p50",
            "label": "视频处理 P50",
            "value": _percentile(lat("motion_processing_ms"), 0.5),
            "unit": "ms",
            "sample_size": len(lat("motion_processing_ms")),
        },
        {
            "key": "pose_valid_rate",
            "label": "关键点有效帧率",
            "value": round(
                sum(lat("pose_keypoint_valid_rate_pct"))
                / len(lat("pose_keypoint_valid_rate_pct")),
                1,
            )
            if lat("pose_keypoint_valid_rate_pct")
            else None,
            "unit": "%",
            "sample_size": len(lat("pose_keypoint_valid_rate_pct")),
        },
        {
            "key": "plan_completion",
            "label": "计划任务完成率",
            "value": _rate(sum(1 for x in plan_items if x.done), len(plan_items)),
            "unit": "%",
            "sample_size": len(plan_items),
        },
        {
            "key": "action_execution",
            "label": "确认动作执行数",
            "value": action_executed,
            "unit": "次",
            "sample_size": len(actions),
        },
        {
            "key": "action_blocked",
            "label": "动作安全拦截数",
            "value": action_blocked,
            "unit": "次",
            "sample_size": len(actions),
        },
        {
            "key": "safety_intercepts",
            "label": "高风险输入拦截数",
            "value": len(safety),
            "unit": "次",
            "sample_size": len(safety),
        },
        {
            "key": "experiments_started",
            "label": "微实验确认启动数",
            "value": len(experiments),
            "unit": "次",
            "sample_size": len(experiments),
        },
        {
            "key": "experiments_active",
            "label": "微实验进行中",
            "value": experiment_status.get("active", 0),
            "unit": "个",
            "sample_size": len(experiments),
        },
        {
            "key": "experiments_completed",
            "label": "微实验完成数",
            "value": experiment_status.get("completed", 0),
            "unit": "个",
            "sample_size": len(experiments),
        },
        {
            "key": "experiments_cancelled",
            "label": "微实验主动停止数",
            "value": experiment_status.get("cancelled", 0),
            "unit": "个",
            "sample_size": len(experiments),
        },
        {
            "key": "experiments_completion_rate",
            "label": "微实验完成率",
            "value": _rate(
                experiment_status.get("completed", 0),
                experiment_status.get("completed", 0) + experiment_status.get("cancelled", 0),
            ),
            "unit": "%",
            "sample_size": experiment_status.get("completed", 0) + experiment_status.get("cancelled", 0),
        },
    ]
    return {
        "window_days": days,
        "runtime_metrics": runtime,
        "benchmarks": latest,
        "experiment_outcomes": experiment_outcomes,
        "notes": [
            "运行数据统计近 30 天的服务请求。",
            "个人尝试只统计你确认开始的周期。",
        ],
    }
