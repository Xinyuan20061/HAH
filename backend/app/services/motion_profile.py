from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now, utc_iso
from app.models import MotionEvent, MotionScore


EXERCISE_LABELS = {
    "squat": "深蹲",
    "pushup": "俯卧撑",
    "lunge": "弓步蹲",
    "leg_abduction": "腿外展",
    "arm_abduction": "直臂侧平举",
    "arm_vw": "手臂 V/W",
}


def _round_average(rows: list[MotionScore], field: str) -> float | None:
    if not rows:
        return None
    return round(sum(float(getattr(row, field)) for row in rows) / len(rows), 1)


def _score_item(row: MotionScore) -> dict:
    try:
        evidence = json.loads(row.evidence_json or "{}")
    except (TypeError, ValueError):
        evidence = {}
    return {
        "id": row.id,
        "job_id": row.job_id,
        "exercise_type": row.exercise_type,
        "exercise_name": EXERCISE_LABELS.get(row.exercise_type, row.exercise_type),
        "requested_exercise_type": row.requested_exercise_type or row.exercise_type,
        "auto_recognized": row.requested_exercise_type == "auto",
        "recognition_method": row.recognition_method or "legacy_user_selected",
        "recognition_confidence": row.recognition_confidence,
        "overall": row.overall,
        "completeness": row.completeness,
        "stability": row.stability,
        "rhythm_control": row.rhythm_control,
        "deviation_index": row.risk_index,
        "confidence": row.confidence,
        "basis": evidence.get("basis", []) if isinstance(evidence, dict) else [],
        "created_at": utc_iso(row.created_at),
    }


def motion_history(db: Session, user_id: int, limit: int = 20) -> list[dict]:
    rows = db.scalars(
        select(MotionScore)
        .where(MotionScore.user_id == user_id)
        .order_by(MotionScore.created_at.desc(), MotionScore.id.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return [_score_item(row) for row in rows]


def motion_profile(db: Session, user_id: int, days: int = 30) -> dict:
    days = max(7, min(days, 365))
    since = utc_now() - timedelta(days=days)
    rows = db.scalars(
        select(MotionScore)
        .where(MotionScore.user_id == user_id, MotionScore.created_at >= since)
        .order_by(MotionScore.created_at.asc(), MotionScore.id.asc())
    ).all()
    events = db.scalars(
        select(MotionEvent).where(
            MotionEvent.user_id == user_id,
            MotionEvent.created_at >= since,
            MotionEvent.severity.in_(["low", "medium", "high"]),
        )
    ).all()
    issues = Counter()
    for event in events:
        try:
            evidence = json.loads(event.evidence_json or "{}")
        except (TypeError, ValueError):
            evidence = {}
        finding = evidence.get("finding") if isinstance(evidence, dict) else None
        if finding:
            issues[str(finding)[:120]] += 1

    by_exercise = []
    for exercise_type in EXERCISE_LABELS:
        exercise_rows = [row for row in rows if row.exercise_type == exercise_type]
        if not exercise_rows:
            continue
        midpoint = max(1, len(exercise_rows) // 2)
        earlier = exercise_rows[:midpoint]
        recent = exercise_rows[midpoint:] or exercise_rows[-1:]
        earlier_avg = _round_average(earlier, "overall")
        recent_avg = _round_average(recent, "overall")
        change = (
            round(recent_avg - earlier_avg, 1)
            if earlier_avg is not None
            and recent_avg is not None
            and len(exercise_rows) >= 2
            else None
        )
        by_exercise.append(
            {
                "exercise_type": exercise_type,
                "exercise_name": EXERCISE_LABELS[exercise_type],
                "sessions": len(exercise_rows),
                "average_score": _round_average(exercise_rows, "overall"),
                "best_score": round(max(row.overall for row in exercise_rows), 1),
                "recent_change_points": change,
            }
        )

    sample_count = len(rows)
    confidence_avg = _round_average(rows, "confidence")
    if sample_count >= 8 and (confidence_avg or 0) >= 0.65:
        quality = "high"
    elif sample_count >= 3:
        quality = "medium"
    else:
        quality = "low"
    overall = _round_average(rows, "overall")
    return {
        "window_days": days,
        "sample_count": sample_count,
        "data_quality": {
            "level": quality,
            "average_confidence": confidence_avg,
            "note": "画像仅基于已完成的视频动作评分；样本不足时不推断力量、耐力或损伤风险。",
        },
        "dimensions": {
            "motion_quality": overall,
            "completion": _round_average(rows, "completeness"),
            "stability": _round_average(rows, "stability"),
            "rhythm_control": _round_average(rows, "rhythm_control"),
            "deviation_index": _round_average(rows, "risk_index"),
        },
        "by_exercise": by_exercise,
        "frequent_findings": [
            {"finding": finding, "count": count}
            for finding, count in issues.most_common(3)
        ],
        "latest": [_score_item(row) for row in reversed(rows[-5:])],
        "summary": (
            f"近 {days} 天完成 {sample_count} 次动作分析，平均动作质量 {overall} 分。"
            if sample_count
            else "暂无动作评分，上传完整动作视频后生成个人运动画像。"
        ),
    }
