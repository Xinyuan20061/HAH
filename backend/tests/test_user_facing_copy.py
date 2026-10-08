from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "reason_code",
    [
        "REVIEW_UNAVAILABLE_OR_UNCERTAIN",
        "UNSUPPORTED_REVIEW",
        "MODEL_DISAGREEMENT",
    ],
)
def test_motion_uncertain_copy_avoids_internal_model_terms(reason_code):
    from app.services.motion.text_summary import fallback_summary

    copy = fallback_summary("uncertain", "squat", reason_code)

    assert "动作类别" in copy
    assert any(action in copy for action in ("重新拍摄", "手动选择"))
    for implementation_term in ("AI", "模型", "视觉复核", "本地候选", reason_code):
        assert implementation_term not in copy


def test_public_job_errors_hide_worker_diagnostics():
    from app.core.time import utc_now
    from app.services.ai_jobs import public_job

    now = utc_now()
    details = "Cloud API HTTP 422 /worker/jobs/7/complete field_path=frames[0].x"
    defaults = {
        "id": 7,
        "media_asset_id": 12,
        "progress": 30,
        "attempts": 2,
        "error_code": "worker_error",
        "error_message": details,
        "result_json": "",
        "created_at": now,
        "started_at": now,
        "finished_at": now,
    }

    failed_food = public_job(
        SimpleNamespace(**defaults, job_type="food_analysis", status="failed")
    )
    assert failed_food["error"] == "餐食识别没有完成，可以手动填写饮食记录。"
    assert details not in failed_food["error"]

    retrying_motion = public_job(
        SimpleNamespace(**defaults, job_type="motion_unified", status="queued")
    )
    assert retrying_motion["error"] == "任务暂时中断，正在重新安排。"
    assert details not in retrying_motion["error"]

def test_privacy_page_copy_uses_plain_language_and_current_storage(
    api, monkeypatch, migrated_engine
):
    from app.api.v1 import privacy as privacy_api
    from app.core.config import settings

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(privacy_api, "engine", migrated_engine)

    response = api.get("/api/v1/privacy/policy")

    assert response.status_code == 200
    policy = response.json()
    assert policy["database"] == (
        "微信云托管服务"
        if migrated_engine.dialect.name == "mysql"
        else "本地测试服务"
    )
    assert policy["media_storage"] == "本地测试存储"
    assert "加密保存在服务端" in policy["api_key"]
    assert "单独征求同意" in policy["ai_processing"]
    user_copy = " ".join(policy.values())
    for implementation_term in (
        "SQLite",
        "CloudBase",
        "COS/S3",
        "VLM_PROVIDER",
        "Worker",
        "DeepSeek",
    ):
        assert implementation_term not in user_copy


def test_privacy_page_copy_names_cloud_service_without_database_jargon(
    api, monkeypatch
):
    from app.api.v1 import privacy as privacy_api
    from app.core.config import settings

    monkeypatch.setattr(settings, "storage_backend", "cloud_ref")
    monkeypatch.setattr(
        privacy_api, "engine", SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    )

    response = api.get("/api/v1/privacy/policy")

    assert response.status_code == 200
    policy = response.json()
    assert policy["database"] == "微信云托管服务"
    assert policy["media_storage"] == "微信云存储"
    assert "MySQL" not in " ".join(policy.values())


def test_health_state_coverage_explanation_does_not_expose_internal_mapping(api):
    response = api.get("/api/v1/health/state")

    assert response.status_code == 200, response.text
    reliability = response.json()["values"]["data_reliability_score"]
    explanation = " ".join(reliability["limitations"])
    assert "健康打卡" in explanation
    assert "饮食" in explanation
    assert "运动" in explanation
    assert "{'checkin':" not in explanation
