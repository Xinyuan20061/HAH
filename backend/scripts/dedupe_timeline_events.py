"""Deduplicate legacy duplicate timeline state events (spec §13.5).

For each (user_id, ref_type, ref_id, event_type) group with more than one row,
keep the latest row (highest id) and remove the rest. Removed rows are first
written to a JSON audit file (full row content) so the cleanup is reversible
from that backup; the MySQL dump taken before this cleanup is the second layer.

The orphaned timeline row pointing at a deleted diet record is NOT removed: it
is historical user-visible data and only its reference is stale.

Usage::

    python scripts/dedupe_timeline_events.py            # dry run, prints plan
    python scripts/dedupe_timeline_events.py --apply    # execute + audit file
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select, func, delete  # noqa: E402
from app.core.database import build_engine  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.models import HealthTimelineEvent  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

AUDIT_DIR = Path(BACKEND_ROOT).parent / "healthmate-prod-backups"


def plan(db):
    groups = db.execute(
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
    remove_ids = []
    audit_rows = []
    for user_id, ref_type, ref_id, event_type, n in groups:
        rows = db.scalars(
            select(HealthTimelineEvent)
            .where(
                HealthTimelineEvent.user_id == user_id,
                HealthTimelineEvent.ref_type == ref_type,
                HealthTimelineEvent.ref_id == ref_id,
                HealthTimelineEvent.event_type == event_type,
            )
            .order_by(HealthTimelineEvent.id.desc())
        ).all()
        keep = rows[0]
        for stale in rows[1:]:
            remove_ids.append(stale.id)
            audit_rows.append(
                {
                    "id": stale.id,
                    "user_id": stale.user_id,
                    "event_type": stale.event_type,
                    "source": stale.source,
                    "ref_type": stale.ref_type,
                    "ref_id": stale.ref_id,
                    "occurred_at": str(stale.occurred_at),
                    "payload_json": stale.payload_json,
                    "removed_reason": "duplicate of latest state event",
                    "kept_id": keep.id,
                }
            )
    return remove_ids, audit_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = build_engine(settings.effective_database_url)
    with Session(engine) as db:
        remove_ids, audit_rows = plan(db)
        print(f"duplicate_groups_removable={len(audit_rows)} ids={len(remove_ids)}")
        if not args.apply:
            print("dry-run: nothing changed. Re-run with --apply to execute.")
            return
        if not remove_ids:
            return
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audit_file = AUDIT_DIR / f"timeline_dedupe_{stamp}.json"
        audit_file.write_text(json.dumps(audit_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        db.execute(delete(HealthTimelineEvent).where(HealthTimelineEvent.id.in_(remove_ids)))
        db.commit()
        print(f"removed={len(remove_ids)} audit={audit_file}")


if __name__ == "__main__":
    main()
