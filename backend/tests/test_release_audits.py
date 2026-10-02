"""WP5 acceptance — the release audits run against a real migrated database.

Spec §13.5/§13.8/§13.12. The audit scripts are only meaningful if they execute on
a schema that matches the migration head, so these tests build a fresh database,
run every audit through the same code path the CI script uses, and additionally
prove the 0026 migration round-trips (spec §15.1 rollback).

A fresh database cannot legitimately exercise "find the anomaly" branches, so the
consistency audit is additionally given deliberately corrupted rows and must
report them.
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic.config import Config
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (populates Base.metadata for the privacy audit)
from app.core.database import build_engine
from app.models import (
    DietRecord,
    FoodAnalysisSession,
    HealthTimelineEvent,
    MediaAsset,
    User,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _upgraded(tmp_path, revision: str = "head"):
    """Migrate a private temporary database to ``revision``.

    ``migrations/env.py`` reads ``settings.effective_database_url``, not the
    alembic.ini value, so the setting must be pointed at the temp file or the
    test would migrate the developer database.
    """
    from alembic import command

    from app.core.config import settings

    url = "sqlite:///" + (tmp_path / "audit.db").as_posix()
    previous = settings.database_url
    settings.database_url = url
    try:
        command.upgrade(_config(url), revision)
    finally:
        settings.database_url = previous
    return url, _config(url), build_engine(url)


def test_audits_pass_on_a_freshly_migrated_database(tmp_path):
    from scripts.audit_data_consistency import _consistency, _privacy

    _url, _config_obj, engine = _upgraded(tmp_path)
    with Session(engine) as db:
        assert _consistency(db) == []
        assert _privacy(db) == []
    engine.dispose()


def test_consistency_audit_detects_real_corruption(tmp_path):
    from scripts.audit_data_consistency import _consistency

    _url, _config_obj, engine = _upgraded(tmp_path)
    with Session(engine) as db:
        user = User(openid="audit-" + tmp_path.name)
        db.add(user)
        db.flush()
        record = DietRecord(
            user_id=user.id,
            name="测试餐",
            meal_type="lunch",
            calories=300,
            recorded_at=None,
        )
        db.add(record)
        db.flush()
        # 1. Two timeline rows for the same business record.
        for _ in range(2):
            db.add(
                HealthTimelineEvent(
                    user_id=user.id,
                    event_type="diet",
                    ref_type="diet",
                    ref_id=record.id,
                    payload_json=json.dumps({"calories": 300}),
                )
            )
        # 2. A timeline row pointing at a record that does not exist.
        db.add(
            HealthTimelineEvent(
                user_id=user.id,
                event_type="diet",
                ref_type="diet",
                ref_id=999999,
                payload_json="{}",
            )
        )
        # 3. A finalized analysis pointing at a record that no longer exists. The
        # FK blocks an INSERT, so write the corruption the way it really happens:
        # a raw UPDATE (manual SQL / restored backup / dropped constraint).
        analysis = FoodAnalysisSession(
            user_id=user.id, status="corrected", initial_json="{}"
        )
        db.add(analysis)
        db.commit()
        from sqlalchemy import text as sql_text

        # SQLite enforces the FK on this connection; a restored backup or manual
        # SQL would not, which is the damage being simulated.
        db.execute(sql_text("PRAGMA foreign_keys=OFF"))
        db.execute(
            sql_text(
                "UPDATE food_analysis_sessions "
                "SET status='finalized', finalized_record_id=888888 WHERE id=:id"
            ),
            {"id": analysis.id},
        )
        db.execute(sql_text("PRAGMA foreign_keys=ON"))
        # 4. A media asset belonging to a deleted account (P0). Its user_id has no
        # FK, which is exactly why the audit must check it.
        db.add(
            MediaAsset(
                user_id=user.id + 5000,
                storage_key="orphan-" + tmp_path.name,
                media_type="image",
            )
        )
        db.commit()
        db.expire_all()

        problems = _consistency(db)
    engine.dispose()

    joined = "\n".join(problems)
    assert "重复时间线事件" in joined
    assert "孤立时间线事件" in joined
    assert "识餐会话" in joined
    assert "P0" in joined


def test_0026_migration_round_trips(tmp_path):
    """Spec §15.1: the new migration must downgrade cleanly when it is empty."""
    from alembic import command

    from app.core.config import settings

    _url, config, engine = _upgraded(tmp_path)
    with engine.connect() as connection:
        from sqlalchemy import inspect

        names = set(inspect(connection).get_table_names())
        columns = {c["name"] for c in inspect(connection).get_columns("diet_records")}
    assert {
        "health_agent_run_stages",
        "agent_action_proposals",
        "media_deletion_tasks",
    } <= names
    assert "version" in columns
    engine.dispose()

    previous = settings.database_url
    settings.database_url = _url
    try:
        command.downgrade(config, "0025_motion_unified_v2_evidence")
    finally:
        settings.database_url = previous
    engine = build_engine(_url)
    with engine.connect() as connection:
        from sqlalchemy import inspect

        names = set(inspect(connection).get_table_names())
        columns = {c["name"] for c in inspect(connection).get_columns("diet_records")}
    engine.dispose()
    assert "agent_action_proposals" not in names
    assert "media_deletion_tasks" not in names
    assert "version" not in columns
