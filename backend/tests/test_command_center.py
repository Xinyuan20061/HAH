import json

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import AIJob, AIWorkerNode


def test_command_center_returns_actionable_empty_state(api):
    response = api.get("/api/v1/health/command-center")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == "3.0"
    assert body["focus"]["key"] == "checkin"
    assert body["focus"]["route"] == "/pages/checkin/index"
    assert body["data_quality"]["score"] is None
    assert body["data_quality"]["sources_observed"] == 0
    assert len(body["plan"]["items"]) == 3


def test_command_center_surfaces_active_job_and_worker_capability(api, migrated_engine):
    with Session(migrated_engine) as db:
        db.add(
            AIWorkerNode(
                worker_id="command-center-worker",
                name="Competition laptop",
                capabilities_json=json.dumps(["motion_pose", "food_vision"]),
                last_seen_at=utc_now(),
            )
        )
        db.add(
            AIJob(
                user_id=api.user_id,
                job_type="motion_pose",
                status="processing",
                progress=45,
                payload_json="{}",
            )
        )
        db.commit()

    body = api.get("/api/v1/health/command-center").json()
    assert body["focus"]["key"] == "active_analysis"
    assert body["focus"]["route"] == "/pages/media/index"
    assert body["ai_system"]["online"] is True
    assert body["ai_system"]["motion_ready"] is True
    assert body["ai_system"]["food_ready"] is True
