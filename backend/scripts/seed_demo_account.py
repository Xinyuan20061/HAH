"""Seed an explicitly-labelled DEMO account with realistic-looking but synthetic
health records, so the Agent v4 loop can be demoed live without claiming any real
recognition.

Everything this script writes is synthetic: no food recognition, no motion
analysis, no real measurement. The demo user's nickname and a timeline event
both say so loudly. This script must never be used to claim "real data".

Usage (backend dir):
  .venv/Scripts/python.exe scripts/seed_demo_account.py
  .venv/Scripts/python.exe scripts/seed_demo_account.py --openid demo-presenter-2026

Idempotent: re-running clears the demo user's prior rows before seeding.
"""

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENV", "test")

# Safety: this script must NEVER write to the production MySQL configured in .env.
# The demo account only exists in the local SQLite demo database.
from app.core.config import settings  # noqa: E402
settings.database_url = "sqlite:///" + str(
    (Path(__file__).resolve().parents[1] / "healthmate.db").resolve()
)
settings.env = "test"

from sqlalchemy import delete, select  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.time import business_today, utc_now  # noqa: E402
from app.models import (  # noqa: E402
    AgentActionAudit,
    AgentMicroExperiment,
    ExerciseRecord,
    HealthCheckIn,
    HealthGoalSetting,
    HealthTimelineEvent,
    User,
)

DEMO_OPENID = "demo-presenter-2026"
DEMO_NICKNAME = "演示账号（数据为演示）"


def _clear_demo_user(db, user_id: int):
    db.execute(delete(HealthCheckIn).where(HealthCheckIn.user_id == user_id))
    db.execute(delete(ExerciseRecord).where(ExerciseRecord.user_id == user_id))
    db.execute(delete(AgentMicroExperiment).where(AgentMicroExperiment.user_id == user_id))
    db.execute(delete(AgentActionAudit).where(AgentActionAudit.user_id == user_id))
    db.execute(delete(HealthTimelineEvent).where(HealthTimelineEvent.user_id == user_id))
    db.commit()


def _seed_demo_experiments(db, uid: int, today):
    """One completed reviewable case, one active, one cancelled, so the live
    demo can show the full loop (active card + review + audit trail).
    """
    now = utc_now()
    db.add(
        AgentMicroExperiment(
            user_id=uid,
            insight_code="record_gap",
            variant="gentle",
            title="3–5 天最小记录实验",
            hypothesis="降低记录负担后，你更容易形成连续记录，从而让后续建议有更充分的数据依据。",
            primary_metric="recorded_days",
            start_date=(today - timedelta(days=8)).isoformat(),
            end_date=(today - timedelta(days=6)).isoformat(),
            status="completed",
            baseline_json='{"value":0,"sample_size":0,"unit":"天"}',
            target_json='{"mode":"threshold","value":2,"unit":"天","requires_baseline":false}',
            protocol_json='{"label":"温和版","days":3,"daily_action":"每天只记录睡眠或饮水中的一项。","measurement":"实验期内至少包含一项真实健康记录的天数","stop_condition":"记录让你感到压力或不适时可随时停止。"}',
            outcome_json=(
                '{"conclusion":"supports_hypothesis","summary":"实验期指标达到预设目标，观察结果支持继续保持这一做法。",'
                '"baseline":{"value":0,"sample_size":0,"unit":"天"},'
                '"followup":{"value":2,"sample_size":2,"unit":"天"},'
                '"delta":2,"target_met":true,'
                '"attribution":"这是单个用户短周期自我观察中的相关变化，不是随机对照试验，不能证明因果。",'
                '"finalized_at":"%s"}' % now.isoformat()
            ),
            completed_at=now,
        )
    )
    db.add(
        AgentMicroExperiment(
            user_id=uid,
            insight_code="sleep_deficit",
            variant="standard",
            title="睡眠恢复微实验",
            hypothesis="连续执行固定的睡前减负动作，可能与平均睡眠时长改善相关。",
            primary_metric="sleep_average",
            start_date=(today - timedelta(days=1)).isoformat(),
            end_date=(today + timedelta(days=4)).isoformat(),
            status="active",
            baseline_json='{"value":5.5,"sample_size":3,"unit":"小时"}',
            target_json='{"mode":"delta","delta":0.5,"value":6.0,"unit":"小时","requires_baseline":true}',
            protocol_json='{"label":"标准版","days":5,"daily_action":"固定起床时间，睡前 45 分钟减少屏幕刺激，并记录当天睡眠。","measurement":"实验期有记录日期的平均睡眠时长","stop_condition":"若持续严重失眠或白天功能明显受损，请停止自我实验并咨询专业人员。"}',
            outcome_json="{}",
        )
    )
    db.add(
        AgentMicroExperiment(
            user_id=uid,
            insight_code="weight_rise",
            variant="gentle",
            title="体重测量一致性实验",
            hypothesis="统一测量条件并连续记录，可能帮助区分真实趋势与单次波动。",
            primary_metric="weight_measurements",
            start_date=(today - timedelta(days=5)).isoformat(),
            end_date=(today - timedelta(days=3)).isoformat(),
            status="cancelled",
            baseline_json='{"value":0,"sample_size":0,"unit":"天"}',
            target_json='{"mode":"threshold","value":2,"unit":"天","requires_baseline":false}',
            protocol_json='{"label":"温和版","days":3,"daily_action":"任选 2 天在相近时间、相近着装下记录体重。","measurement":"实验期内在相近条件下完成体重记录的天数","stop_condition":"若称重引发明显焦虑，请立即停止。"}',
            outcome_json='{"conclusion":"cancelled_by_user","attribution":"用户主动停止；已有健康记录保持不变。"}',
            completed_at=now,
        )
    )
    for key, status in (
        ("experiment.start", "executed"),
        ("experiment.finish", "executed"),
        ("experiment.start", "executed"),
        ("experiment.start", "executed"),
        ("experiment.cancel", "executed"),
    ):
        db.add(
            AgentActionAudit(
                user_id=uid,
                action_key=key,
                risk_level="low",
                requires_confirmation=True,
                status=status,
                input_json='{"demo":true}',
                output_json='{"demo":true}',
            )
        )
    db.commit()


def main():
    parser = argparse.ArgumentParser(description="Seed a labelled demo account.")
    parser.add_argument("--openid", default=DEMO_OPENID)
    args = parser.parse_args()

    # Ensure the local SQLite demo DB is migrated to head before seeding.
    from alembic import command  # noqa: E402
    from alembic.config import Config  # noqa: E402

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.openid == args.openid))
        if user is None:
            user = User(openid=args.openid, nickname=DEMO_NICKNAME)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user.nickname = DEMO_NICKNAME
            db.add(user)
            db.commit()
        uid = user.id
        _clear_demo_user(db, uid)

        today = business_today()

        # Baseline 7 calm days (D-13 .. D-7): normal sleep, steady weight.
        for back in range(13, 6, -1):
            day = today - timedelta(days=back)
            db.add(
                HealthCheckIn(
                    user_id=uid,
                    record_date=day.isoformat(),
                    water_ml=1800,
                    sleep_hours=7.5,
                    weight_kg=68.0,
                    steps=8200,
                    mood="calm",
                )
            )

        # D-6: one real-looking exercise day (the last one), then a 5-day stall.
        last_exercise = today - timedelta(days=6)
        db.add(
            HealthCheckIn(
                user_id=uid,
                record_date=last_exercise.isoformat(),
                water_ml=1600,
                sleep_hours=7.0,
                weight_kg=68.0,
                steps=6000,
                mood="good",
            )
        )
        exercise_row = ExerciseRecord(
            user_id=uid,
            name="快走",
            duration_min=20,
            calories_burned=120,
            intensity="low",
            recorded_at=datetime.combine(last_exercise, time(8, 0, 0)),
        )
        db.add(exercise_row)

        # D-5 .. D-1: no exercise, sleep trending short (<6h).
        short_sleeps = [5.2, 5.5, 5.8, 5.3, 5.5]
        for i, hours in enumerate(short_sleeps, start=1):
            day = today - timedelta(days=i)
            db.add(
                HealthCheckIn(
                    user_id=uid,
                    record_date=day.isoformat(),
                    water_ml=1400,
                    sleep_hours=hours,
                    steps=3000,
                    mood="tired",
                )
            )

        # Weight: last three weigh-ins strictly rising (D-2, D-1, today).
        db.commit()
        for back, weight in ((2, 68.0), (1, 68.4), (0, 68.8)):
            day = today - timedelta(days=back)
            existing = db.scalar(
                select(HealthCheckIn).where(
                    HealthCheckIn.user_id == uid,
                    HealthCheckIn.record_date == day.isoformat(),
                )
            )
            if existing:
                existing.weight_kg = weight
                db.add(existing)
            else:
                db.add(
                    HealthCheckIn(
                        user_id=uid,
                        record_date=day.isoformat(),
                        water_ml=1500,
                        sleep_hours=5.5 if back == 0 else 5.3,
                        weight_kg=weight,
                        steps=3200,
                        mood="tired",
                    )
                )

        # Goals so exercise_stall can fire (needs exercise_target > 0).
        goal = db.scalar(select(HealthGoalSetting).where(HealthGoalSetting.user_id == uid))
        if goal is None:
            db.add(HealthGoalSetting(user_id=uid, exercise_target=150, sleep_target=8, weekly_checkin_target=5))
        else:
            goal.exercise_target = 150
            db.add(goal)

        # Loud provenance marker.
        db.add(
            HealthTimelineEvent(
                user_id=uid,
                event_type="demo_data_seeded",
                source="script",
                payload_json=(
                    '{"note":"本账号健康记录由 seed_demo_account.py 合成，'
                    '用于演示 Agent v4 微实验闭环；不代表真实测量、真实识别或真实用户。"}'
                ),
            )
        )
        db.commit()

        # Demo micro-experiment loop: one completed review + one active + one cancelled.
        _seed_demo_experiments(db, uid, today)

        print("=" * 64)
        print("演示数据已灌入（全部为脚本合成，非真实识别）：")
        print(f"  openid      : {args.openid}")
        print(f"  nickname    : {DEMO_NICKNAME}")
        print(f"  user_id     : {uid}")
        print(f"  日期范围    : {(today - timedelta(days=13)).isoformat()} .. {today.isoformat()}")
        print("  预期主动信号: exercise_stall(运动断档5天) / sleep_deficit(连续<6h) / weight_rise(体重连升3次)")
        print("  微实验案例  : 1 个已完成(supports_hypothesis) + 1 个进行中(sleep_deficit) + 1 个已取消(weight_rise)")
        print("  注意        : 不含动作分析与识餐识别；演示时不得说成真实 AI 识别结果。")
        print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
