from datetime import date, datetime

import httpx
import pytest
from sqlalchemy.orm import Session
from test_reliability import claim, job, leased_body

from app.core.config import Settings, settings
from app.core.json_output import json_object
from app.core.time import business_day, utc_day_bounds
from app.models import DietRecord
from app.services.health_data import daily_facts


def test_fully_configured_production_is_valid():
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="dual_client_cloud",
        mobile_auth_enabled=True,
        mobile_upload_enabled=True,
        database_url="mysql+pymysql://test:test@localhost/db?charset=utf8mb4",
        storage_backend="cloud_ref",
        mobile_upload_backend="s3",
        s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
        s3_access_key="cos-access-key",
        s3_secret_key="cos-secret-key",
        s3_bucket="healthmate-test-1250000000",
        s3_region="ap-shanghai",
        secret_key="a" * 48,
        credentials_encryption_key="b" * 48,
        worker_token="c" * 48,
        wechat_app_id="wx-test",
        wechat_app_secret="test",
        mobile_wechat_app_id="wx-mobile-test",
        mobile_wechat_app_secret="mobile-test",
        cloudbase_env_id="test-env",
        cloudbase_custom_login_credentials_json='{"env_id":"test-env","private_key_id":"test-key","private_key":"test-pem"}',
        cloudbase_storage_secret_id="test-storage-id",
        cloudbase_storage_secret_key="test-storage-key",
        cloudrun_service_name="healthmate-api",
        public_base_url="https://test.example",
    )
    assert config.configuration_errors() == []
    assert "test:test" not in str(config.safe_summary())


def test_s3_cos_mobile_route_preserves_legacy_cloudbase_backend():
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="dual_client_cloud",
        mobile_auth_enabled=True,
        mobile_upload_enabled=True,
        database_url="mysql+pymysql://test:test@localhost/db?charset=utf8mb4",
        storage_backend="cloud_ref",
        mobile_upload_backend="s3",
        s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
        s3_access_key="cos-access-key",
        s3_secret_key="cos-secret-key",
        s3_bucket="healthmate-test-1250000000",
        s3_region="ap-shanghai",
        secret_key="a" * 48,
        credentials_encryption_key="b" * 48,
        worker_token="c" * 48,
        wechat_app_id="wx-test",
        wechat_app_secret="test",
        mobile_wechat_app_id="wx-mobile-test",
        mobile_wechat_app_secret="mobile-test",
        cloudbase_env_id="test-env",
        cloudbase_custom_login_credentials_json='{"env_id":"test-env","private_key_id":"test-key","private_key":"test-pem"}',
        cloudbase_storage_secret_id="test-storage-id",
        cloudbase_storage_secret_key="test-storage-key",
        cloudrun_service_name="healthmate-api",
        public_base_url="https://test.example",
    )
    assert config.configuration_errors() == []
    assert config.safe_summary()["s3_storage_configured"] is True
    assert config.safe_summary()["storage_backend"] == "cloud_ref"
    assert config.safe_summary()["mobile_upload_backend"] == "s3"


def test_legacy_wechat_profile_does_not_require_mobile_or_cos_credentials():
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="wechat_cloud",
        database_url="mysql+pymysql://test:test@localhost/db?charset=utf8mb4",
        storage_backend="cloud_ref",
        mobile_upload_backend="cloud_ref",
        secret_key="a" * 48,
        credentials_encryption_key="b" * 48,
        worker_token="c" * 48,
        wechat_app_id="wx-test",
        wechat_app_secret="test",
        cloudbase_env_id="test-env",
        cloudbase_storage_secret_id="test-storage-id",
        cloudbase_storage_secret_key="test-storage-key",
        cloudrun_service_name="healthmate-api",
        public_base_url="https://test.example",
    )
    assert config.configuration_errors() == []


def test_s3_cos_production_rejects_missing_storage_credentials():
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="dual_client_cloud",
        mobile_auth_enabled=True,
        mobile_upload_enabled=True,
        database_url="mysql+pymysql://test:test@localhost/db?charset=utf8mb4",
        storage_backend="cloud_ref",
        mobile_upload_backend="s3",
        secret_key="a" * 48,
        credentials_encryption_key="b" * 48,
        worker_token="c" * 48,
        wechat_app_id="wx-test",
        wechat_app_secret="test",
        mobile_wechat_app_id="wx-mobile-test",
        mobile_wechat_app_secret="mobile-test",
        cloudbase_env_id="test-env",
        cloudbase_custom_login_credentials_json='{"env_id":"test-env","private_key_id":"test-key","private_key":"test-pem"}',
        cloudrun_service_name="healthmate-api",
        public_base_url="https://test.example",
    )
    errors = "\n".join(config.configuration_errors())
    assert "S3_ACCESS_KEY" in errors
    assert "S3_SECRET_KEY" in errors


@pytest.mark.parametrize(
    "bucket",
    [
        "healthmate-media",
        "HealthMate-1250000000",
        "healthmate-media-",
        "healthmate-media-１２３４５６",
    ]
)
def test_android_cos_production_requires_bucket_name_with_app_id(bucket):
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="dual_client_cloud",
        mobile_upload_enabled=True,
        mobile_upload_backend="s3",
        s3_bucket=bucket,
    )

    assert any("S3_BUCKET" in error for error in config.configuration_errors())


def test_android_cos_production_rejects_endpoint_region_mismatch():
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="dual_client_cloud",
        mobile_upload_enabled=True,
        mobile_upload_backend="s3",
        s3_bucket="healthmate-media-1250000000",
        s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
        s3_region="ap-beijing",
    )

    assert any("S3_REGION" in error for error in config.configuration_errors())


def test_mobile_production_profile_requires_explicit_feature_flags():
    config = Settings(
        _env_file=None,
        env="production",
        deployment_profile="dual_client_cloud",
        database_url="mysql+pymysql://test:test@localhost/db?charset=utf8mb4",
        storage_backend="cloud_ref",
        mobile_upload_backend="s3",
        secret_key="a" * 48,
        credentials_encryption_key="b" * 48,
        worker_token="c" * 48,
        wechat_app_id="wx-test",
        wechat_app_secret="test",
        cloudbase_env_id="test-env",
        cloudbase_storage_secret_id="storage-id",
        cloudbase_storage_secret_key="storage-secret",
        cloudrun_service_name="healthmate-api",
        public_base_url="https://test.example",
    )
    errors = "\n".join(config.configuration_errors())
    assert "MOBILE_AUTH_ENABLED" in errors
    assert "MOBILE_UPLOAD_ENABLED" in errors


def test_unset_production_profile_preserves_legacy_wechat_behavior():
    config = Settings(_env_file=None, env="production")
    assert config.effective_deployment_profile == "wechat_cloud"
    assert config.safe_summary()["mobile_auth_enabled"] is False
    assert config.safe_summary()["mobile_upload_enabled"] is False


def test_cloudbase_admin_credentials_must_be_paired():
    config = Settings(
        _env_file=None,
        env="development",
        cloudbase_storage_secret_id="only-one-half",
    )
    assert any(
        "CLOUDBASE_STORAGE_SECRET_ID" in error
        for error in config.configuration_errors()
    )


def test_health_days_use_beijing_calendar_and_utc_storage(api, migrated_engine):
    recorded = datetime(2026, 1, 1, 23, 30)
    assert business_day(recorded) == date(2026, 1, 2)
    assert utc_day_bounds(date(2026, 1, 2))[0] == datetime(2026, 1, 1, 16)
    with Session(migrated_engine) as db:
        db.add(
            DietRecord(
                user_id=api.user_id, name="late", recorded_at=recorded, calories=200
            )
        )
        db.commit()
        rows = daily_facts(db, api.user_id, date(2026, 1, 1), date(2026, 1, 2))
        assert rows[0]["calories"] is None and rows[1]["calories"] == 200


def test_finalized_ai_diet_can_be_deleted_without_foreign_key_error(api, food_result):
    created, _, _ = job(api)
    claimed = claim(api)
    api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/complete",
        json={**leased_body(claimed), "result": food_result},
        headers=api.worker_headers,
    )
    result = api.get(f"/api/v1/vision/food-jobs/{created['job_id']}").json()["result"]
    analysis_id = result["analysis_id"]
    finalized = api.post(
        f"/api/v1/vision/food-analysis/{analysis_id}/finalize", json={"confirmed": True}
    ).json()
    assert (
        api.delete(f"/api/v1/diet/records/{finalized['record']['id']}").status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/vision/food-analysis/{analysis_id}/finalize",
            json={"confirmed": True},
        ).status_code
        == 409
    )


def test_expired_download_can_refresh_in_place_without_consuming_attempt(api):
    created, asset, route = job(api)
    claimed = claim(api)
    response = api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/fail",
        headers=api.worker_headers,
        json={
            **leased_body(claimed),
            "error_code": "media_url_expired",
            "retryable": False,
        },
    )
    assert response.json()["status"] == "waiting_source_refresh"
    value = api.get(f"/api/v1{route}/{created['job_id']}").json()
    assert value["attempts"] == 0
    assert (
        api.put(
            f"/api/v1/media/{asset['media_id']}/refresh-source",
            json={"temp_url": "https://example.com/fresh.jpg"},
        ).status_code
        == 200
    )
    assert claim(api)["job_id"] == created["job_id"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_text_provider_failures_are_safe_and_explicit(api, monkeypatch, status):
    monkeypatch.setattr(settings, "deepseek_api_key", "test-private-value")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="secret-upstream-detail")
            ),
            **kwargs,
        ),
    )
    response = api.post("/api/v1/chat", json={"message": "你好"})
    assert response.status_code == 503
    assert (
        "secret-upstream-detail" not in response.text
        and "test-private-value" not in response.text
    )


def test_wechat_code_exchange_with_test_transport_and_jwt(api, monkeypatch):
    monkeypatch.setattr(settings, "wechat_app_id", "wx-test")
    monkeypatch.setattr(settings, "wechat_app_secret", "test-app-secret")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"openid": "test-wechat-openid"}
                )
            ),
            **kwargs,
        ),
    )
    response = api.post("/api/v1/auth/wechat", json={"code": "test-one-time-code"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert (
        api.get(
            "/api/v1/users/me", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 200
    )
    assert "test-app-secret" not in response.text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[]", {}),
        ("null", {}),
        ('"plain text"', {}),
        (
            '```json\n{"plan":{"items":[]},"reply":"ok"}\n```',
            {"plan": {"items": []}, "reply": "ok"},
        ),
        ('{"reply":"one"} and {"reply":"two"}', {}),
    ],
)
def test_text_json_requires_one_object(text, expected):
    assert json_object(text) == expected


def test_workout_input_validation_is_422_not_500(api):
    response = api.post(
        "/api/v1/insights/workout-plan", json={"days": "bad", "minutes": -1}
    )
    assert response.status_code == 422


def test_food_contract_matches_worker():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert (root / "backend/app/schemas/ai_results.py").read_text(encoding="utf8") == (
        root / "ai-worker/healthmate_worker/results.py"
    ).read_text(encoding="utf8")


def test_key_rotation_failure_is_explicit_and_business_still_works(api, monkeypatch):
    monkeypatch.setattr(settings, "credentials_encryption_key", "first-test-key")
    assert (
        api.put(
            "/api/v1/users/me/ai-config",
            json={"enabled": True, "api_key": "test-private-key"},
        ).status_code
        == 200
    )
    monkeypatch.setattr(settings, "credentials_encryption_key", "second-test-key")
    assert api.get("/api/v1/users/me/ai-config").status_code == 503
    assert api.post("/api/v1/users/me/ai-config/test", json={}).status_code == 503
    assert api.get("/api/v1/diet/records").status_code == 200


def test_food_requires_explicit_confirmation(api, food_result):
    created, _, _ = job(api)
    claimed = claim(api)
    api.post(
        f"/api/v1/worker/jobs/{created['job_id']}/complete",
        json={**leased_body(claimed), "result": food_result},
        headers=api.worker_headers,
    )
    value = api.get(f"/api/v1/vision/food-jobs/{created['job_id']}").json()["result"]
    response = api.post(
        f"/api/v1/vision/food-analysis/{value['analysis_id']}/finalize", json={}
    )
    assert response.status_code == 200 and response.json()["ok"] is False
    assert api.get("/api/v1/diet/records").json()["items"] == []


def test_utc_boundary_accepts_offset_and_serializes_z():
    from pydantic import TypeAdapter

    from app.core.time import UTCDateTime

    adapter = TypeAdapter(UTCDateTime)
    value = adapter.validate_python("2026-01-02T08:00:00+08:00")
    assert value == datetime(2026, 1, 2)
    assert adapter.dump_json(value) == b'"2026-01-02T00:00:00Z"'


@pytest.mark.parametrize(
    "path, payload",
    [
        ("/users/me/health-profile", {"allergies": "a" * 256}),
        ("/diet/records", {"name": "test", "calories": 20, "portion": "a" * 121}),
        ("/diet/records", {"name": "test", "calories": 20, "protein": 501}),
        (
            "/exercise/records",
            {"name": "test", "duration_min": 20, "intensity": "a" * 21},
        ),
    ],
)
def test_invalid_business_fields_fail_before_mysql_write(api, path, payload):
    method = api.put if "health-profile" in path else api.post
    assert method("/api/v1" + path, json=payload).status_code == 422
