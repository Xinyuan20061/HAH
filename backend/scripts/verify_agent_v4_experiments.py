"""Agent v4 micro-experiment end-to-end verification (exit gate for Day 1-2).

Runs the full HTTP stack (FastAPI TestClient) against an isolated SQLite
database migrated to head (0019_agent_micro_experiments) and proves:

  Scenario A (normal experiment): record_gap -> confirm start -> real records
                                  -> finish -> supports_hypothesis
  Scenario B (insufficient data): confirm start -> no records -> finish
                                  -> insufficient_data (no positive claim)
  Plus: active-stop (cancel) and evaluation dashboard only counting confirmed
        real samples.

Usage:
  backend/.venv/Scripts/python.exe backend/scripts/verify_agent_v4_experiments.py

Exit code 0 when both scenarios and the dashboard assertions pass.
"""

import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["ENV"] = "test"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, build_engine, get_db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.core.time import business_today  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AgentActionAudit,
    AgentMicroExperiment,
    HealthCheckIn,
    User,
)
from app.services.agent.experiments import build_experiment_proposal  # noqa: E402


def _new_user(db: Session) -> User:
    user = User(openid="verify-" + os.urandom(8).hex())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_checkin(db: Session, user_id: int, record_date: str, **fields) -> None:
    db.add(HealthCheckIn(user_id=user_id, record_date=record_date, **fields))
    db.commit()


def _force_expiry(db: Session, experiment_id: int) -> None:
    """Simulate an experiment that started earlier and reaches its end today."""
    row = db.get(AgentMicroExperiment, experiment_id)
    today = business_today()
    row.start_date = (today - timedelta(days=1)).isoformat()
    row.end_date = today.isoformat()
    db.commit()


def _audit_keys(db: Session, user_id: int) -> list[str]:
    rows = db.scalars(
        select(AgentActionAudit)
        .where(AgentActionAudit.user_id == user_id)
        .order_by(AgentActionAudit.id)
    ).all()
    return [r.action_key for r in rows]


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="agent_v4_verify_"))
    url = "sqlite:///" + (workdir / "api.db").as_posix()

    previous_url = settings.database_url
    settings.database_url = url
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "head")
    settings.database_url = previous_url
    engine = build_engine(url)

    def dependency():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    report = {"migrated_head": "0019_agent_micro_experiments", "scenarios": {}}

    with Session(engine) as db:
        user_a = _new_user(db)
        user_a_id = user_a.id
        user_b_id = _new_user(db).id

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + create_access_token(str(user_a_id))

        # ---- Scenario A: normal experiment, real records, supports_hypothesis ----
        insights = client.get("/api/v1/agent/insights").json()
        record_gap = next(i for i in insights["insights"] if i["code"] == "record_gap")
        proposal = build_experiment_proposal("record_gap")
        assert record_gap["experiment_proposal"]["primary_metric"] == "recorded_days"
        assert [v["key"] for v in proposal["variants"]] == ["gentle", "standard"]

        started = client.post(
            "/api/v1/agent/experiments",
            json={"insight_code": "record_gap", "variant": "gentle"},
        )
        assert started.status_code == 200, started.text
        exp_a = started.json()["experiment"]
        assert exp_a["status"] == "active"

        with Session(engine) as db:
            today = business_today()
            _add_checkin(db, user_a_id, (today - timedelta(days=1)).isoformat(), water_ml=1200, sleep_hours=7.0)
            _add_checkin(db, user_a_id, today.isoformat(), water_ml=1500, sleep_hours=7.5)
            _force_expiry(db, exp_a["id"])

        finished = client.post(f"/api/v1/agent/experiments/{exp_a['id']}/finish", json={})
        assert finished.status_code == 200, finished.text
        result_a = finished.json()["experiment"]
        assert result_a["status"] == "completed"
        assert result_a["outcome"]["conclusion"] == "supports_hypothesis"
        assert result_a["progress"]["target_met"] is True
        assert result_a["progress"]["sample_size"] == 2
        assert "不能证明因果" in result_a["outcome"]["attribution"]

        with Session(engine) as db:
            keys_a = _audit_keys(db, user_a_id)
        assert keys_a == ["experiment.start", "experiment.finish"]

        report["scenarios"]["A_normal_experiment"] = {
            "status": result_a["status"],
            "conclusion": result_a["outcome"]["conclusion"],
            "target_met": result_a["progress"]["target_met"],
            "sample_size": result_a["progress"]["sample_size"],
            "audit": keys_a,
        }

        # ---- Scenario B: insufficient data, no positive claim ----
        client.headers["Authorization"] = "Bearer " + create_access_token(str(user_b_id))
        started_b = client.post(
            "/api/v1/agent/experiments",
            json={"insight_code": "record_gap", "variant": "gentle"},
        )
        assert started_b.status_code == 200, started_b.text
        exp_b = started_b.json()["experiment"]

        # Active stop is allowed any time; original records are untouched.
        cancelled = client.post(f"/api/v1/agent/experiments/{exp_b['id']}/cancel", json={})
        assert cancelled.status_code == 200
        assert cancelled.json()["experiment"]["status"] == "cancelled"

        # Restart for the insufficient-data finish path.
        started_b2 = client.post(
            "/api/v1/agent/experiments",
            json={"insight_code": "record_gap", "variant": "gentle"},
        )
        assert started_b2.status_code == 200, started_b2.text
        exp_b2 = started_b2.json()["experiment"]
        with Session(engine) as db:
            _force_expiry(db, exp_b2["id"])
        finished_b = client.post(f"/api/v1/agent/experiments/{exp_b2['id']}/finish", json={})
        assert finished_b.status_code == 200, finished_b.text
        result_b = finished_b.json()["experiment"]
        assert result_b["outcome"]["conclusion"] == "insufficient_data"
        assert result_b["outcome"]["followup"]["sample_size"] == 0
        assert "记录不足" in result_b["outcome"]["summary"]
        assert result_b["progress"]["target_met"] is False

        with Session(engine) as db:
            keys_b = _audit_keys(db, user_b_id)
        assert keys_b == [
            "experiment.start",
            "experiment.cancel",
            "experiment.start",
            "experiment.finish",
        ]

        report["scenarios"]["B_insufficient_data"] = {
            "active_stop_status": "cancelled",
            "conclusion": result_b["outcome"]["conclusion"],
            "summary": result_b["outcome"]["summary"],
            "target_met": result_b["progress"]["target_met"],
            "sample_size": result_b["outcome"]["followup"]["sample_size"],
            "audit": keys_b,
        }

        # ---- Dashboard counts only confirmed real samples ----
        client.headers["Authorization"] = "Bearer " + create_access_token(str(user_a_id))
        dash = client.get("/api/v1/evaluation/dashboard").json()
        by_key = {m["key"]: m for m in dash["runtime_metrics"]}
        assert by_key["experiments_started"]["value"] == 1
        assert by_key["experiments_completed"]["value"] == 1
        assert by_key["experiments_completion_rate"]["value"] == 100.0
        assert dash["experiment_outcomes"] == {"supports_hypothesis": 1}

        client.headers["Authorization"] = "Bearer " + create_access_token(str(user_b_id))
        dash_b = client.get("/api/v1/evaluation/dashboard").json()
        by_key_b = {m["key"]: m for m in dash_b["runtime_metrics"]}
        assert by_key_b["experiments_started"]["value"] == 2
        assert by_key_b["experiments_cancelled"]["value"] == 1
        assert by_key_b["experiments_completed"]["value"] == 1
        assert dash_b["experiment_outcomes"] == {
            "cancelled_by_user": 1,
            "insufficient_data": 1,
        }

        report["dashboard"] = {
            "user_a": {"started": 1, "completed": 1, "outcomes": dash["experiment_outcomes"]},
            "user_b": {
                "started": 2,
                "cancelled": 1,
                "completed": 1,
                "outcomes": dash_b["experiment_outcomes"],
            },
        }

    app.dependency_overrides.clear()
    engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("RESULT: PASS — Agent v4 正常实验与数据不足实验均按契约完成，未确认样本不计入评测。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
