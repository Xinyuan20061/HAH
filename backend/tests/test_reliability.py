from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, timezone
from uuid import uuid4
import base64
import hashlib
import json
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.config import Settings, settings
from app.core.time import utc_now, utc_iso, naive_utc
from app.core.security import create_access_token
from app.core.database import build_engine
from app.models import (
    AIJob,
    MediaAsset,
    FoodAnalysisSession,
    DietRecord,
    User,
    MotionScore,
    MotionEvent,
    ExerciseResource,
    KnowledgeDocument,
)
from app.services.ai_jobs import (
    claim_next_job,
    create_ai_job,
    requeue_expired_jobs,
    extend_lease,
)
from app.core.url_security import validate_ai_base_url


def register(api, media_type="image"):
    suffix, mime = (
        (".jpg", "image/jpeg") if media_type == "image" else (".mp4", "video/mp4")
    )
    file_id = (
        f"cloud://test.env/healthmate/u{api.user_id}/{media_type}/{uuid4().hex}{suffix}"
    )
    data = {
        "file_id": file_id,
        "temp_url": "https://example.com/source" + suffix,
        "media_type": media_type,
        "original_name": "test" + suffix,
        "content_type": mime,
        "size_bytes": 100,
    }
    response = api.post("/api/v1/media/register-cloud", json=data)
    assert response.status_code == 200, response.text
    return response.json(), data


def job(api, media_type="image"):
    asset, data = register(api, media_type)
    route = "/vision/food-jobs" if media_type == "image" else "/media/motion-jobs"
    response = api.post("/api/v1" + route, json={"media_id": asset["media_id"]})
    assert response.status_code == 200
    return response.json(), asset, route


def claim(api, capability="food_vision", **extra):
    response = api.post(
        "/api/v1/worker/jobs/claim",
        headers=api.worker_headers,
        json={"worker_id": "test-worker", "capabilities": [capability], **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()["job"]


def leased_body(claimed):
    return {"worker_id": "test-worker", "lease_token": claimed["lease_token"]}


def test_production_preflight_lists_all_missing_fields_and_never_selects_sqlite():
    config = Settings(_env_file=None, env="production", database_url="")
    errors = "\n".join(config.configuration_errors())
    for name in [
        "DATABASE_URL",
        "SECRET_KEY",
        "CREDENTIALS_ENCRYPTION_KEY",
        "WORKER_TOKEN",
        "WECHAT_APP_ID",
        "STORAGE_BACKEND",
    ]:
        assert name in errors
    assert not config.effective_database_url.startswith("sqlite")
    assert not config.run_migrations_on_start


@pytest.mark.parametrize("url", ["sqlite:///prod.db", "postgresql://example/db", ""])
def test_production_rejects_non_mysql(url):
    config = Settings(_env_file=None, env="production", database_url=url)
    assert any("DATABASE_URL" in error for error in config.configuration_errors())


def test_development_sqlite_is_explicitly_allowed():
    config = Settings(_env_file=None, env="development", database_url="")
    assert not config.configuration_errors()
    assert config.effective_database_url.startswith("sqlite")


def test_encryption_key_must_be_independent():
    config = Settings(
        _env_file=None,
        env="production",
        secret_key="a" * 48,
        credentials_encryption_key="a" * 48,
    )
    assert any("与 SECRET_KEY 不同" in error for error in config.configuration_errors())


def test_health_checks_worker_offline_and_cors(api):
    assert api.get("/health/live").status_code == 200
    response = api.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database_backend"] in {"sqlite", "mysql"}
    response = api.options(
        "/api/v1/users/me",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-credentials" not in response.headers


def test_readiness_unmigrated_database_returns_503_and_live_is_independent(
    api, monkeypatch, tmp_path
):
    import app.main as main

    engine = build_engine("sqlite:///" + (tmp_path / "empty.db").as_posix())
    monkeypatch.setattr(main, "engine", engine)
    assert api.get("/health/live").status_code == 200
    response = api.get("/health/ready")
    assert (
        response.status_code == 503
        and response.json()["checks"][0]["detail"] == "migration_required"
    )
    engine.dispose()


def test_login_profile_records_and_utc(api):
    assert api.get("/api/v1/users/me").json()["id"] == api.user_id
    assert (
        api.put("/api/v1/users/me/health-profile", json={"age": 25}).status_code == 200
    )
    response = api.post("/api/v1/diet/records", json={"name": "早餐", "calories": 200})
    assert response.status_code == 200 and response.json()["recorded_at"].endswith("Z")
    assert api.get("/api/v1/diet/records").json()[0]["name"] == "早餐"


@pytest.mark.parametrize("token", ["invalid.jwt.token", "expired", "subject"])
def test_auth_invalid_expired_and_non_numeric_subject(api, monkeypatch, token):
    if token == "expired":
        monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
        token = create_access_token(str(api.user_id))
    elif token == "subject":
        token = create_access_token("not-an-int")
    assert (
        api.get(
            "/api/v1/users/me", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


def test_production_dev_login_and_cloud_header_login_disabled(api, monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    assert api.post("/api/v1/auth/dev-login", json={}).status_code == 404
    assert (
        api.post(
            "/api/v1/auth/cloud-login", headers={"x-wx-openid": "forged"}
        ).status_code
        == 404
    )


def test_worker_auth_and_validation_do_not_echo_submitted_secrets(api):
    data = {
        "worker_id": "test-worker",
        "capabilities": ["motion_pose"],
        "metadata": {
            "semantic_model": {
                "configured": True,
                "available": True,
                "mode": "trained_model",
                "model_key": "skeleton-stgcn-multilabel",
                "version": "1.0.0",
                "sha256": "must-not-be-public",
            }
        },
    }
    assert api.post("/api/v1/worker/heartbeat", json=data).status_code == 401
    assert (
        api.post(
            "/api/v1/worker/heartbeat", json=data, headers={"X-Worker-Token": "bad"}
        ).status_code
        == 401
    )
    assert (
        api.post(
            "/api/v1/worker/heartbeat", json=data, headers=api.worker_headers
        ).status_code
        == 200
    )
    worker = api.get("/api/v1/system/ai-worker").json()["nodes"][0]
    assert worker["semantic_model"]["available"] is True
    assert worker["semantic_model"]["model_key"] == "skeleton-stgcn-multilabel"
    assert "sha256" not in worker["semantic_model"]
    response = api.put(
        "/api/v1/users/me/ai-config",
        json={"api_key": "private-test-value", "base_url": "x"},
    )
    assert response.status_code in {400, 422}
    assert "private-test-value" not in response.text


def test_media_register_idempotency_and_ownership(api):
    asset, data = register(api)
    assert (
        api.post("/api/v1/media/register-cloud", json=data).json()["media_id"]
        == asset["media_id"]
    )
    data["file_id"] = data["file_id"].replace(f"/u{api.user_id}/", "/u999999/")
    assert api.post("/api/v1/media/register-cloud", json=data).status_code == 403


@pytest.mark.parametrize(
    "change,status",
    [
        ({"size_bytes": 999999999}, 413),
        ({"media_type": "video"}, 403),
        ({"content_type": "image/svg+xml"}, 400),
        ({"file_id": "https://example.com/x"}, 400),
    ],
)
def test_media_validation(api, change, status):
    _, data = register(api)
    data.update(change)
    assert api.post("/api/v1/media/register-cloud", json=data).status_code == status


def test_food_closed_loop_progress_complete_correct_finalize_and_privacy(
    api, migrated_engine, food_result
):
    created, asset, route = job(api)
    claimed = claim(api)
    assert claimed["job_id"] == created["job_id"]
    body = leased_body(claimed)
    assert (
        api.post(
            f"/api/v1/worker/jobs/{claimed['job_id']}/progress",
            json={**body, "progress": 50},
            headers=api.worker_headers,
        ).status_code
        == 200
    )
    endpoint = f"/api/v1/worker/jobs/{claimed['job_id']}/complete"
    assert (
        api.post(
            endpoint, json={**body, "result": food_result}, headers=api.worker_headers
        ).status_code
        == 200
    )
    assert api.post(
        endpoint, json={**body, "result": food_result}, headers=api.worker_headers
    ).json()["already_completed"]
    result = api.get(f"/api/v1{route}/{claimed['job_id']}").json()
    assert result["status"] == "done" and result["finished_at"].endswith("Z")
    assert result["result"]["is_estimate"] and result["result"]["warning"]
    analysis_id = result["result"]["analysis_id"]
    assert (
        api.put(
            f"/api/v1/vision/food-analysis/{analysis_id}/correct",
            json={"dish_name": "人工校正", "calories": 400},
        ).status_code
        == 200
    )
    finalized = api.post(
        f"/api/v1/vision/food-analysis/{analysis_id}/finalize", json={"confirmed": True}
    ).json()
    assert finalized["ok"]
    repeat = api.post(
        f"/api/v1/vision/food-analysis/{analysis_id}/finalize", json={"confirmed": True}
    ).json()
    assert repeat["already_finalized"]
    assert (
        api.post(
            "/api/v1/vision/food-jobs", json={"media_id": asset["media_id"]}
        ).json()["job_id"]
        == claimed["job_id"]
    )
    response = api.request(
        "DELETE", "/api/v1/privacy/account", json={"confirmation": "DELETE MY DATA"}
    )
    assert response.status_code == 409
    response = api.request(
        "DELETE",
        "/api/v1/privacy/account",
        json={
            "confirmation": "DELETE MY DATA",
            "cloud_files_deleted": [asset["cloud_file_id"]],
        },
    )
    assert (
        response.status_code == 200
        and response.json()["cloud_media_deletion"] == "client_reported"
    )
    assert api.get("/api/v1/users/me").status_code == 401


def test_food_item_correction_recomputes_and_persists_evidence(
    api, migrated_engine, food_result
):
    created, _, _ = job(api)
    claimed = claim(api)
    response = api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(claimed), "result": food_result},
    )
    assert response.status_code == 200, response.text
    analysis_id = api.get(
        f"/api/v1/vision/food-jobs/{created['job_id']}"
    ).json()["result"]["analysis_id"]
    items = [
        {"name": "米饭", "calories": 220, "weight_g": 160, "evidence": "可见一碗米饭"},
        {"name": "鸡胸", "calories": 180, "weight_g": 120, "evidence": "可见切片鸡胸"},
    ]
    corrected = api.put(
        f"/api/v1/vision/food-analysis/{analysis_id}/correct",
        json={"dish_name": "米饭配鸡胸", "items": items},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["corrected"]["calories"] == 400
    finalized = api.post(
        f"/api/v1/vision/food-analysis/{analysis_id}/finalize",
        json={"confirmed": True},
    ).json()
    with Session(migrated_engine) as db:
        record = db.get(DietRecord, finalized["record_id"])
        assert record.calories == 400
        assert [item["name"] for item in record.items] == ["米饭", "鸡胸"]


def test_motion_preview_is_validated_served_and_not_duplicated_in_event(
    api, migrated_engine
):
    created, _, route = job(api, "video")
    claimed = claim(api, "motion_pose")
    image = b"\xff\xd8bounded-test-jpeg\xff\xd9"
    encoded = base64.b64encode(image).decode("ascii")
    result = {
        "pose": {
            "available": True,
            "reps": 1,
            "keypoint_valid_rate": 0.9,
            "errors": [],
        },
        "frames": [
            {
                "timestamp": 1.0,
                "event": "squat_bottom",
                "image_b64": encoded,
                "image_mime": "image/jpeg",
                "preview_sha256": hashlib.sha256(image).hexdigest(),
                "face_anonymized": True,
            }
        ],
        "method": "test",
    }
    response = api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(claimed), "result": result},
    )
    assert response.status_code == 200, response.text
    public = api.get(f"/api/v1{route}/{created['job_id']}").json()["result"]
    assert public["frames"][0]["url"].startswith("data:image/jpeg;base64,")
    assert "image_b64" not in public["frames"][0]
    with Session(migrated_engine) as db:
        event = db.scalar(select(MotionEvent).where(MotionEvent.job_id == created["job_id"]))
        assert event and "image_b64" not in event.evidence_json


def test_motion_previews_expire_but_structured_evidence_remains(
    api, migrated_engine
):
    with Session(migrated_engine) as db:
        item = AIJob(
            user_id=api.user_id,
            job_type="motion_pose",
            status="done",
            finished_at=utc_now() - timedelta(days=8),
            result_json=json.dumps(
                {
                    "pose": {"available": True},
                    "frames": [
                        {
                            "event": "squat_bottom",
                            "timestamp": 1.0,
                            "image_b64": "unused",
                            "image_mime": "image/jpeg",
                            "finding": "最低点",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        db.add(item)
        db.commit()
        requeue_expired_jobs(db)
        db.refresh(item)
        result = json.loads(item.result_json)
        assert result["frames"][0]["preview_expired"] is True
        assert "image_b64" not in result["frames"][0]
        assert result["frames"][0]["finding"] == "最低点"


def test_motion_closed_loop_and_invalid_result(api):
    created, _, route = job(api, "video")
    claimed = claim(api, "motion_pose")
    endpoint = f"/api/v1/worker/jobs/{claimed['job_id']}/complete"
    body = leased_body(claimed)
    assert (
        api.post(
            endpoint, json={**body, "result": {}}, headers=api.worker_headers
        ).status_code
        == 422
    )
    result = {
        "pose": {
            "available": True,
            "reps": 2,
            "keypoint_valid_rate": 0.9,
            "errors": [],
        },
        "frames": [{"timestamp": 1.0, "event": "pushup_bottom"}],
        "method": "test-only worker",
    }
    assert (
        api.post(
            endpoint, json={**body, "result": result}, headers=api.worker_headers
        ).status_code
        == 200
    )
    assert (
        api.get(f"/api/v1{route}/{created['job_id']}").json()["result"]["pose"]["reps"]
        == 2
    )


def test_motion_score_events_are_persisted_and_resources_are_curated(
    api, migrated_engine
):
    created, _, _ = job(api, "video")
    claimed = claim(api, "motion_pose")
    result = {
        "pose": {
            "available": True,
            "reps": 1,
            "keypoint_valid_rate": 0.92,
            "errors": [],
        },
        "score": {
            "available": True,
            "completeness": 88,
            "stability": 90,
            "rhythm_control": 80,
            "risk_index": 8,
            "overall": 87,
            "confidence": 0.86,
            "basis": ["test evidence"],
        },
        "frames": [
            {
                "timestamp": 1.25,
                "event": "squat_bottom",
                "stage": "最低点",
                "finding": "已到达最低位置",
                "advice": "控制节奏",
            }
        ],
        "method": "test-only worker",
    }
    response = api.post(
        f"/api/v1/worker/jobs/{claimed['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(claimed), "result": result},
    )
    assert response.status_code == 200, response.text
    with Session(migrated_engine) as db:
        score = db.scalar(
            select(MotionScore).where(MotionScore.job_id == created["job_id"])
        )
        events = db.scalars(
            select(MotionEvent).where(MotionEvent.job_id == created["job_id"])
        ).all()
        assert score and score.overall == 87 and score.exercise_type == "squat"
        assert score.requested_exercise_type == "squat"
        assert score.recognition_method == "legacy_user_selected"
        assert len(events) == 1 and events[0].event_type == "squat_bottom"
        db.add(
            ExerciseResource(
                exercise_type="squat",
                title="审核过的深蹲教学",
                platform="B站",
                url="https://www.bilibili.com/video/BV1N5411b72P/",
                difficulty="beginner",
                tags_json='["标准动作"]',
                quality_score=92,
                summary="测试资源",
                active=True,
            )
        )
        db.commit()

    resources = api.get("/api/v1/exercise-resources", params={"query": "深蹲怎么做"})
    assert resources.status_code == 200
    body = resources.json()
    assert body["url_policy"] == "curated_database_only"
    assert body["resources"] and all(
        x["source"] == "curated_database" for x in body["resources"]
    )

    agent = api.post("/api/v1/agent/respond", json={"message": "深蹲怎么做？"})
    assert agent.status_code == 200
    assert agent.json()["intent"] == "exercise_knowledge"
    assert agent.json()["resources"]


def test_auto_motion_recognition_persists_detected_type_and_can_abstain(
    api, migrated_engine
):
    assert api.put(
        "/api/v1/fitness/training-intent",
        json={"target_body_parts": ["chest"], "goals": ["strength"]},
    ).status_code == 200
    asset, _ = register(api, "video")
    created = api.post(
        "/api/v1/media/motion-jobs",
        json={"media_id": asset["media_id"], "exercise_type": "auto"},
    )
    assert created.status_code == 200, created.text
    claimed = claim(api, "motion_pose")
    invalid_recognition = {
        "pose": {"available": False, "message": "invalid test"},
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": [],
            "accepted": False,
            "confidence": 0,
            "margin": 0,
            "method": "rule_feature_matching_v1",
            "candidates": [],
        },
    }
    assert api.post(
        f"/api/v1/worker/jobs/{claimed['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(claimed), "result": invalid_recognition},
    ).status_code == 422
    recognition = {
        "mode": "auto",
        "requested_type": "auto",
        "selected_type": "pushup",
        "accepted": True,
        "confidence": 0.82,
        "margin": 24.0,
        "method": "rule_feature_matching_v1",
        "candidates": [
            {"exercise_type": "pushup", "match_score": 88, "rank": 1},
            {"exercise_type": "squat", "match_score": 64, "rank": 2},
        ],
        "reason": "俯卧撑特征领先",
    }
    result = {
        "pose": {
            "available": True,
            "exercise_type": "pushup",
            "reps": 2,
            "keypoint_valid_rate": 0.91,
            "errors": [],
        },
        "score": {
            "available": True,
            "completeness": 85,
            "stability": 82,
            "rhythm_control": 79,
            "risk_index": 10,
            "overall": 82,
            "confidence": 0.84,
        },
            "recognition": recognition,
            "compositional_semantics": {
                "available": True,
                "method": "pose_compositional_rules_v1",
                "confidence": 0.78,
                "orientation": {"key": "horizontal", "name": "躯干接近水平"},
                "laterality": {"key": "bilateral", "name": "双侧近似对称模式"},
                "movement_patterns": [
                    {
                        "key": "horizontal_upper_body",
                        "name": "水平体位上肢动作",
                        "score": 88.0,
                        "evidence": "肘角活动范围约 80°",
                    }
                ],
                "observed_regions": [
                    {"key": "upper_body", "name": "上肢", "basis": "检测到明显肘关节屈伸"}
                ],
                "evidence": {"valid_samples": 12},
                "scope": "可观察动作语义，不代表动作名称、肌肉激活或训练效果",
            },
            "frames": [],
        "method": "test auto recognition",
    }
    response = api.post(
        f"/api/v1/worker/jobs/{claimed['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(claimed), "result": result},
    )
    assert response.status_code == 200, response.text
    public_result = api.get(
        f"/api/v1/media/motion-jobs/{claimed['job_id']}"
    ).json()["result"]
    semantics = public_result["training_semantics"]
    assert semantics["exercise_name"] == "俯卧撑"
    assert semantics["goal_alignment"]["status"] == "aligned"
    assert "胸部" in semantics["goal_alignment"]["matched_body_parts"]
    stored_semantics = api.get(
        f"/api/v1/fitness/motion-semantics/{claimed['job_id']}"
    )
    assert stored_semantics.status_code == 200
    assert stored_semantics.json()["observed_motion"]["movement_patterns"][0]["key"] == "horizontal_upper_body"
    with Session(migrated_engine) as db:
        score = db.scalar(
            select(MotionScore).where(MotionScore.job_id == claimed["job_id"])
        )
        assert score.exercise_type == "pushup"
        assert score.requested_exercise_type == "auto"
        assert score.recognition_method == "rule_feature_matching_v1"
        assert score.recognition_confidence == pytest.approx(0.82)

    second_asset, _ = register(api, "video")
    second = api.post(
        "/api/v1/media/motion-jobs",
        json={"media_id": second_asset["media_id"], "exercise_type": "auto"},
    ).json()
    second_claim = claim(api, "motion_pose")
    rejected = {
        "pose": {"available": False, "message": "候选接近", "errors": []},
        "score": {"available": False, "reason": "候选接近"},
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": None,
            "accepted": False,
            "confidence": 0,
            "margin": 3,
            "method": "rule_feature_matching_v1",
            "candidates": [
                {"exercise_type": "squat", "match_score": 61, "rank": 1},
                {"exercise_type": "lunge", "match_score": 58, "rank": 2},
            ],
            "reason": "候选接近",
        },
        "frames": [],
    }
    response = api.post(
        f"/api/v1/worker/jobs/{second_claim['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(second_claim), "result": rejected},
    )
    assert response.status_code == 200, response.text
    assert second_claim["job_id"] == second["job_id"]
    with Session(migrated_engine) as db:
        assert db.scalar(
            select(MotionScore).where(MotionScore.job_id == second["job_id"])
        ) is None

def test_motion_profile_closes_history_agent_loop(api, migrated_engine):
    with Session(migrated_engine) as db:
        for overall in [62, 78, 84]:
            job_row = AIJob(
                user_id=api.user_id,
                job_type="motion_pose",
                status="done",
                payload_json='{"exercise_type":"squat"}',
            )
            db.add(job_row)
            db.flush()
            db.add(
                MotionScore(
                    user_id=api.user_id,
                    job_id=job_row.id,
                    exercise_type="squat",
                    completeness=overall,
                    stability=overall - 2,
                    rhythm_control=overall - 4,
                    risk_index=max(0, 100 - overall),
                    overall=overall,
                    confidence=0.8,
                    evidence_json='{"basis":["测试证据"]}',
                )
            )
        db.commit()
    profile = api.get("/api/v1/fitness/motion-profile?days=30")
    assert profile.status_code == 200
    body = profile.json()
    assert body["sample_count"] == 3
    assert body["by_exercise"][0]["recent_change_points"] > 0
    assert body["dimensions"]["motion_quality"] == pytest.approx(74.7)
    history = api.get("/api/v1/fitness/motion-history?limit=2").json()["items"]
    assert len(history) == 2 and history[0]["overall"] == 84
    context = api.get("/api/v1/agent/context").json()
    assert context["motion_profile"]["sample_count"] == 3


def test_missing_health_data_is_not_treated_as_failed_diet_goal(api):
    today = api.get("/api/v1/health/today").json()
    assert today["observed"] == {
        "checkin": False,
        "diet": False,
        "exercise": False,
        "plan": False,
    }
    plan = api.get("/api/v1/health/plan/today").json()
    assert plan["items"][0]["task_key"] == "diet_record"


def test_audited_knowledge_search_and_agent_citations(api, migrated_engine):
    with Session(migrated_engine) as db:
        db.add_all(
            [
                KnowledgeDocument(
                    source_key="test-who-activity",
                    title="WHO 身体活动指南",
                    organization="世界卫生组织（WHO）",
                    source_url="https://www.who.int/publications/i/item/9789240015128",
                    source_published_at="2020-11-25",
                    section="成年人",
                    content="成年人每周宜进行150至300分钟中等强度有氧活动，并每周至少2天进行力量活动。",
                    tags_json='["每周运动","有氧","力量训练"]',
                    active=True,
                ),
                KnowledgeDocument(
                    source_key="test-nhc-diet",
                    title="中国居民膳食指南",
                    organization="国家卫生健康委员会",
                    source_url="https://www.nhc.gov.cn/example",
                    source_published_at="2022-06-08",
                    section="平衡膳食",
                    content="保持食物多样，并少盐、少油、控糖。",
                    tags_json='["膳食","营养","少盐少油"]',
                    active=True,
                ),
            ]
        )
        db.commit()

    response = api.get(
        "/api/v1/knowledge/search", params={"query": "每周应该运动多久"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["retrieval"] == "audited_hybrid_v2"
    assert body["items"][0]["source"] == "audited_knowledge_database"
    assert body["items"][0]["citation_id"] == "K1"
    assert body["items"][0]["url"].startswith("https://www.who.int/")

    agent = api.post("/api/v1/agent/respond", json={"message": "每周应该运动多久？"})
    assert agent.status_code == 200
    result = agent.json()
    assert result["knowledge_sources"]
    assert "knowledge_documents" in result["facts_used"]
    assert result["knowledge_sources"][0]["url"].startswith("https://www.who.int/")


def test_knowledge_search_does_not_return_unrelated_sources(api, migrated_engine):
    with Session(migrated_engine) as db:
        db.add(
            KnowledgeDocument(
                source_key="test-only-diet",
                title="平衡膳食指南",
                organization="权威机构",
                source_url="https://example.org/diet",
                source_published_at="2022-01-01",
                section="饮食",
                content="保持食物多样。",
                tags_json='["膳食","营养"]',
                active=True,
            )
        )
        db.commit()
    body = api.get(
        "/api/v1/knowledge/search", params={"query": "如何修理自行车链条"}
    ).json()
    assert body["items"] == []
def test_chat_uses_recent_session_history(api, monkeypatch):
    captured = []

    class Provider:
        async def chat(self, system, message):
            captured.append(message)
            return type("Result", (), {"text": "测试回答", "provider": "test"})()

    monkeypatch.setattr("app.api.v1.chat.get_provider", lambda user: Provider())
    first = api.post("/api/v1/chat", json={"message": "我今天练了深蹲"})
    assert first.status_code == 200
    session_id = first.json()["session_id"]
    second = api.post(
        "/api/v1/chat",
        json={"message": "那明天呢？", "session_id": session_id},
    )
    assert second.status_code == 200
    assert "我今天练了深蹲" in captured[-1]
    assert "测试回答" in captured[-1]
    assert "当前用户问题：那明天呢？" in captured[-1]


def test_costly_ai_endpoints_are_rate_limited(api, monkeypatch):
    from app.core.rate_limit import limiter

    limiter._buckets.clear()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ai_per_minute", 1)
    # Deterministic "no AI configured" environment: exclude the dev-machine
    # local fallback engine so the 503 contract stays stable.
    monkeypatch.setattr(settings, "local_llm_model_dir", "")
    first = api.post("/api/v1/chat", json={"message": "普通问题"})
    assert first.status_code == 503
    second = api.post("/api/v1/chat", json={"message": "再次请求"})
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    limiter._buckets.clear()


@pytest.mark.parametrize(
    "change",
    [
        {"calories": -1},
        {"confidence": 1.1},
        {"tips": "invalid"},
        {"fat": "NaN"},
        {"estimated_weight_g": 10000},
    ],
)
def test_food_result_rejects_invalid_data(api, food_result, change):
    created, _, _ = job(api)
    claimed = claim(api)
    response = api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(claimed), "result": {**food_result, **change}},
    )
    assert response.status_code == 422


def test_expired_source_waits_and_refresh_resumes_original_job(api, migrated_engine):
    created, asset, route = job(api)
    with Session(migrated_engine) as db:
        source = db.get(MediaAsset, asset["media_id"])
        source.source_url_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert claim(api) is None
    response = api.get(f"/api/v1{route}/{created['job_id']}").json()
    assert response["status"] == "waiting_source_refresh" and response["attempts"] == 0
    refreshed = api.put(
        f"/api/v1/media/{asset['media_id']}/refresh-source",
        json={"temp_url": "https://example.com/fresh.jpg"},
    )
    assert refreshed.json()["resumed_jobs"] == 1
    assert claim(api)["job_id"] == created["job_id"]


def test_lease_recovery_and_stale_worker_cannot_finish(
    api, migrated_engine, food_result
):
    created, _, _ = job(api)
    original = claim(api)
    with Session(migrated_engine) as db:
        value = db.get(AIJob, created["job_id"])
        value.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    reclaimed = claim(api)
    assert (
        reclaimed["job_id"] == original["job_id"]
        and reclaimed["lease_token"] != original["lease_token"]
    )
    response = api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/complete",
        headers=api.worker_headers,
        json={**leased_body(original), "result": food_result},
    )
    assert response.status_code == 409


def test_retry_backoff_exhaustion_and_non_retryable_failure(api, migrated_engine):
    created, _, _ = job(api)
    for attempt in range(settings.worker_max_attempts):
        claimed = claim(api)
        assert claimed is not None
        response = api.post(
            f"/api/v1/worker/jobs/{created['job_id']}/fail",
            headers=api.worker_headers,
            json={
                **leased_body(claimed),
                "error_code": "vlm_timeout",
                "retryable": True,
            },
        )
        assert response.json()["status"] == (
            "failed" if attempt + 1 == settings.worker_max_attempts else "queued"
        )
        if attempt + 1 < settings.worker_max_attempts:
            assert claim(api) is None
            with Session(migrated_engine) as db:
                row = db.get(AIJob, created["job_id"])
                row.next_attempt_at = utc_now() - timedelta(seconds=1)
                db.commit()
    assert claim(api) is None
    created, _, _ = job(api)
    claimed = claim(api)
    response = api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/fail",
        headers=api.worker_headers,
        json={**leased_body(claimed), "error_code": "invalid_media", "retryable": True},
    )
    assert response.json()["status"] == "failed"


def test_claim_request_id_is_idempotent(api):
    created, _, _ = job(api)
    request_id = uuid4().hex
    first = claim(api, request_id=request_id)
    second = claim(api, request_id=request_id)
    assert first["job_id"] == second["job_id"] == created["job_id"]
    assert first["lease_token"] == second["lease_token"]


def test_concurrent_workers_claim_one_job_once(api, migrated_engine):
    created, _, _ = job(api)

    def take(index):
        with Session(migrated_engine) as db:
            value = claim_next_job(
                db, worker_id=f"concurrent-{index}", capabilities=["food_vision"]
            )
            return value.id if value else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(take, range(4)))
    assert results.count(created["job_id"]) == 1


def test_unavailable_ai_is_explicit_and_does_not_break_records(api, monkeypatch):
    # Deterministic "no AI configured" environment: exclude the dev-machine
    # local fallback engine so the 503 contract stays stable.
    monkeypatch.setattr(settings, "local_llm_model_dir", "")
    response = api.post("/api/v1/chat", json={"message": "今天吃什么"})
    assert response.status_code == 503 and response.json()["code"] == 30001
    assert (
        api.post(
            "/api/v1/diet/records", json={"name": "手动记录", "calories": 100}
        ).status_code
        == 200
    )
    response = api.post("/api/v1/chat/stream", json={"message": "你好"})
    assert response.status_code == 503


def test_user_key_is_encrypted_and_not_returned(api, migrated_engine):
    value = "test-private-key-value"
    response = api.put(
        "/api/v1/users/me/ai-config", json={"enabled": True, "api_key": value}
    )
    assert response.status_code == 200 and value not in response.text
    assert value not in api.get("/api/v1/users/me/ai-config").text
    with Session(migrated_engine) as db:
        user = db.get(User, api.user_id)
        assert user.ai_config.api_key_encrypted != value


def test_user_voice_key_is_encrypted_and_returned_as_hint_only(api, migrated_engine):
    value = "voice-private-key-value"
    response = api.put(
        "/api/v1/users/me/ai-config",
        json={
            "enabled": False,
            "voice_enabled": True,
            "voice_base_url": "https://voice.example.com/v1",
            "voice_stt_model": "whisper-1",
            "voice_tts_model": "tts-1",
            "voice_name": "alloy",
            "voice_api_key": value,
        },
    )
    assert response.status_code == 200
    assert response.json()["has_voice_api_key"] is True
    assert value not in response.text
    assert value not in api.get("/api/v1/users/me/ai-config").text
    with Session(migrated_engine) as db:
        user = db.get(User, api.user_id)
        assert user.ai_config.voice_api_key_encrypted != value


def test_saved_voice_config_can_be_tested_without_returning_audio(api, monkeypatch):
    response = api.put(
        "/api/v1/users/me/ai-config",
        json={
            "enabled": False,
            "voice_enabled": True,
            "voice_base_url": "https://voice.example.com/v1",
            "voice_stt_model": "whisper-1",
            "voice_tts_model": "tts-1",
            "voice_name": "alloy",
            "voice_api_key": "voice-test-secret",
        },
    )
    assert response.status_code == 200

    class FakeVoiceProvider:
        def __init__(self, api_key, base_url, stt_model, tts_model, voice):
            assert api_key == "voice-test-secret"
            assert base_url == "https://voice.example.com/v1"
            assert tts_model == "tts-1"
            assert voice == "alloy"

        async def synthesize(self, text):
            from app.harness.voice import SpeechAudio

            assert text == "连接成功"
            return SpeechAudio(b"private-audio")

    from app.api.v1 import ai_config

    monkeypatch.setattr(ai_config, "OpenAICompatibleVoiceProvider", FakeVoiceProvider)
    tested = api.post("/api/v1/users/me/ai-config/voice-test", json={})
    assert tested.status_code == 200
    assert tested.json()["ok"] is True
    assert "audio_base64" not in tested.text
    assert "private-audio" not in tested.text


def test_utc_normalizes_aware_and_naive_without_schema_change():
    aware = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    assert naive_utc(aware) == datetime(2026, 1, 1)
    assert utc_iso(aware) == "2026-01-01T00:00:00Z"


def test_production_ai_dns_ssrf_rejected(monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError):
        validate_ai_base_url("https://public-name.example")
