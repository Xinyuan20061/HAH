"""Data-consistency audit (spec §13.5) and privacy-coverage audit (§13.8/§10).

Both audits are read-only and print a machine-checkable report:

* referential integrity: orphaned foreign keys, duplicate timeline events for the
  same business record, ``finalized_record_id`` pointing at a deleted record,
  negative or impossible nutrition values;
* privacy coverage: every model/media-bearing column must be classified as
  exported, deleted, or retained-with-a-reason, so a new media field cannot ship
  without entering the retention plan.

Usage::

    python scripts/audit_data_consistency.py [--database-url sqlite:///./x.db]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.core.database import Base, build_engine  # noqa: E402
import app.models  # noqa: E402,F401  (populates Base.metadata)
from app.models import (  # noqa: E402
    DietRecord,
    FoodAnalysisSession,
    HealthTimelineEvent,
    MediaAsset,
    MotionAnalysisRun,
    MotionEvidenceFrame,
    User,
)


def _consistency(db) -> list[str]:
    problems: list[str] = []

    # 1. Duplicate timeline events for one business record.
    duplicates = db.execute(
        select(
            HealthTimelineEvent.user_id,
            HealthTimelineEvent.ref_type,
            HealthTimelineEvent.ref_id,
            HealthTimelineEvent.event_type,
            func.count().label("n"),
        )
        .where(HealthTimelineEvent.ref_id.is_not(None))
        .group_by(
            HealthTimelineEvent.user_id,
            HealthTimelineEvent.ref_type,
            HealthTimelineEvent.ref_id,
            HealthTimelineEvent.event_type,
        )
        .having(func.count() > 1)
    ).all()
    for row in duplicates:
        problems.append(
            f"重复时间线事件 user={row.user_id} {row.ref_type}#{row.ref_id} "
            f"{row.event_type} x{row.n}（编辑应改写同一条事件）"
        )

    # 2. A timeline row pointing at a record that no longer exists.
    diet_ids = {row[0] for row in db.execute(select(DietRecord.id)).all()}
    orphans = db.execute(
        select(HealthTimelineEvent.id, HealthTimelineEvent.ref_id)
        .where(HealthTimelineEvent.ref_type == "diet")
        .where(HealthTimelineEvent.event_type == "diet")
    ).all()
    for row in orphans:
        if row.ref_id not in diet_ids:
            problems.append(f"孤立时间线事件 id={row.id} 指向不存在的饮食记录 #{row.ref_id}")

    # 3. A food-analysis session finalized onto a record that is gone.
    for session in db.scalars(select(FoodAnalysisSession)).all():
        if session.finalized_record_id and session.finalized_record_id not in diet_ids:
            problems.append(
                f"识餐会话 #{session.id} 仍指向已删除记录 #{session.finalized_record_id}"
            )

    # 4. Impossible nutrition values (the DB CHECK only covers meal_type).
    for record in db.scalars(select(DietRecord)).all():
        if record.calories < 0 or record.protein < 0 or record.carbs < 0 or record.fat < 0:
            problems.append(f"饮食记录 #{record.id} 存在负营养值")
        if record.items and len(record.items) > 12:
            problems.append(f"饮食记录 #{record.id} 食材项超过 12 条")
        if record.meal_type not in {"breakfast", "lunch", "dinner", "snack", "other"}:
            problems.append(f"饮食记录 #{record.id} 餐次非法: {record.meal_type}")

    # 5. Orphaned media references.
    asset_ids = {row[0] for row in db.execute(select(MediaAsset.id)).all()}
    for run in db.scalars(select(MotionAnalysisRun)).all():
        if run.media_asset_id not in asset_ids:
            problems.append(f"动作分析 #{run.id} 指向不存在的媒体资产 #{run.media_asset_id}")
    for frame in db.scalars(select(MotionEvidenceFrame)).all():
        if frame.run_id is None:
            problems.append(f"证据帧 #{frame.id} 缺少所属运行")

    # 6. Accounts whose media assets outlived them.
    user_ids = {row[0] for row in db.execute(select(User.id)).all()}
    for asset in db.scalars(select(MediaAsset)).all():
        if asset.user_id not in user_ids:
            problems.append(
                f"P0: 媒体资产 #{asset.id} 属于已删除账户 #{asset.user_id}，"
                "必须进入对账隔离清单"
            )
    return problems


def _privacy(db) -> list[str]:
    """Reuse the single privacy manifest (no second classifier to drift)."""
    from scripts.audit_privacy_coverage import collect as collect_privacy

    return collect_privacy()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    from app.core.config import settings

    url = args.database_url or settings.effective_database_url
    engine = build_engine(url)
    failures: list[str] = []
    with engine.connect() as connection:
        from sqlalchemy.orm import Session

        with Session(connection) as db:
            for label, items in (
                ("数据一致性", _consistency(db)),
                ("隐私覆盖", _privacy(db)),
            ):
                print(f"[{label}] 问题 {len(items)} 项")
                for item in items:
                    print("  -", item)
                failures.extend(items)
    engine.dispose()

    if failures:
        print("\nFAIL")
        return 1
    print("OK: 无孤立引用 / 重复时间线 / 未分类敏感列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
