"""Cross-account isolation checks using independent fixture users and JWTs.

The development login intentionally maps every request to one shared user, so
these tests create two users directly instead of treating dev-login as evidence
of account isolation.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.models import HealthCheckIn, User


def test_two_users_keep_checkins_and_deletion_isolated(api, migrated_engine):
    with Session(migrated_engine) as db:
        first = User(openid="isolation-a-" + uuid4().hex, nickname="隔离用户甲")
        second = User(openid="isolation-b-" + uuid4().hex, nickname="隔离用户乙")
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

    first_headers = {"Authorization": f"Bearer {create_access_token(str(first_id))}"}
    second_headers = {"Authorization": f"Bearer {create_access_token(str(second_id))}"}

    first_initial = api.get("/api/v1/health/checkin/today", headers=first_headers)
    second_initial = api.get("/api/v1/health/checkin/today", headers=second_headers)
    assert first_initial.status_code == second_initial.status_code == 200
    assert first_initial.json()["id"] is None
    assert second_initial.json()["id"] is None

    first_saved = api.put(
        "/api/v1/health/checkin/today",
        headers=first_headers,
        json={
            "water_ml": 900,
            "sleep_hours": 6.5,
            "weight_kg": 62.3,
            "steps": 5100,
            "mood": "tired",
        },
    )
    second_saved = api.put(
        "/api/v1/health/checkin/today",
        headers=second_headers,
        json={
            "water_ml": 1600,
            "sleep_hours": 8.0,
            "weight_kg": 71.5,
            "steps": 10400,
            "mood": "good",
        },
    )
    assert first_saved.status_code == second_saved.status_code == 200

    first_read = api.get("/api/v1/health/checkin/today", headers=first_headers)
    second_read = api.get("/api/v1/health/checkin/today", headers=second_headers)
    assert first_read.json()["water_ml"] == 900
    assert first_read.json()["weight_kg"] == 62.3
    assert second_read.json()["water_ml"] == 1600
    assert second_read.json()["weight_kg"] == 71.5
    assert first_read.json()["id"] != second_read.json()["id"]

    deleted = api.request(
        "DELETE",
        "/api/v1/privacy/account",
        headers=first_headers,
        json={"confirmation": "DELETE MY DATA"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["cloud_media_deletion"] == "no_cloud_files"
    assert (
        api.get("/api/v1/health/checkin/today", headers=first_headers).status_code
        == 401
    )

    surviving_read = api.get("/api/v1/health/checkin/today", headers=second_headers)
    assert surviving_read.status_code == 200
    assert surviving_read.json()["water_ml"] == 1600
    assert surviving_read.json()["weight_kg"] == 71.5

    with Session(migrated_engine) as db:
        assert db.get(User, first_id) is None
        assert db.get(User, second_id) is not None
        rows = db.scalars(
            select(HealthCheckIn).where(
                HealthCheckIn.user_id.in_([first_id, second_id])
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].user_id == second_id
