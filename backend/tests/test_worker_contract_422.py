# -*- coding: utf-8 -*-
"""P0-A: MotionWorkerResultV1 receipt contract + 422 diagnostics regression.

The fixtures below are REDACTED structural replicas of the receipt class that
produced the screenshot 422 (job 45). They contain no real video/image bytes:
JPEG previews are 1x1 synthetic JPEGs and base64 is only used where the contract
actually exercises the decoder.
"""

import base64
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import AIJob
from app.schemas.worker import (
    MOTION_WORKER_RESULT_SCHEMA_VERSION,
    MotionResultSchemaError,
    motion_result_first_error_path,
)

LEASE = "test-lease-token-1234"
WORKER = "healthmate-laptop-01"


def _seed_job(migrated_engine, user_id, payload):
    with Session(migrated_engine) as db:
        job = AIJob(
            user_id=user_id,
            job_type="motion_pose",
            status="processing",
            worker_id=WORKER,
            lease_token=LEASE,
            lease_expires_at=utc_now() + timedelta(minutes=5),
            payload_json=__import__("json").dumps(payload),
        )
        db.add(job)
        db.commit()
        return job.id


def _tiny_jpeg_b64():
    # Smallest valid JPEG (FFD8 ... FFD9), well under 80KB.
    raw = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
        "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
        "AAAAAAAAAAAAAAAAAAAACP/aAAgBAQABPwH/2Q=="
    )
    assert raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
    return base64.b64encode(raw).decode("ascii")


def valid_v1_result(requested_type="auto"):
    return {
        "schema_version": MOTION_WORKER_RESULT_SCHEMA_VERSION,
        "pose": {
            "available": False,
            "message": "测试视频证据不足，暂不评价",
            "errors": [],
        },
        "frames": [],
        "score": {"available": False},
        "recognition": {
            "mode": "auto",
            "requested_type": "auto",
            "selected_type": None,
            "accepted": False,
            "confidence": 0.2,
            "margin": 12.0,
            "method": "rules_v1",
            "candidates": [],
        },
    }


def _post(api, job_id, result):
    return api.post(
        f"/api/v1/worker/jobs/{job_id}/complete",
        headers=api.worker_headers,
        json={
            "worker_id": WORKER,
            "lease_token": LEASE,
            "result": result,
            "metrics": {"latency_ms": 1.0},
        },
    )


def test_valid_v1_receipt_is_accepted(migrated_engine, api):
    job_id = _seed_job(migrated_engine, api.user_id, {"exercise_type": "auto"})
    resp = _post(api, job_id, valid_v1_result())
    assert resp.status_code == 200, resp.text


def test_old_worker_without_schema_version_still_accepted(migrated_engine, api):
    # Compat window: no schema_version -> legacy validation path, still 200.
    job_id = _seed_job(migrated_engine, api.user_id, {"exercise_type": "auto"})
    old = valid_v1_result()
    old.pop("schema_version")
    resp = _post(api, job_id, old)
    assert resp.status_code == 200, resp.text


def test_unknown_schema_version_is_rejected_with_path(migrated_engine, api):
    job_id = _seed_job(migrated_engine, api.user_id, {"exercise_type": "auto"})
    result = valid_v1_result()
    result["schema_version"] = "motion-worker-result-v999"
    resp = _post(api, job_id, result)
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "MOTION_RESULT_SCHEMA_INVALID"
    assert body["retryable"] is False
    assert body["details"]["field_path"] == "schema_version"
    assert body["request_id"]


def _score_while_abstained(result):
    # accepted=False but score.available=True -> contract violation.
    result["score"] = {
        "available": True,
        "completeness": 80.0,
        "stability": 80.0,
        "rhythm_control": 80.0,
        "risk_index": 10.0,
        "overall": 80.0,
        "confidence": 0.8,
    }
    return result


@pytest.mark.parametrize(
    "mutate,expected_path_fragment",
    [
        # Missing required field: pose.available
        (lambda r: r["pose"].pop("available"), "pose"),
        # Disallowed extra field: external image URL on a frame
        (lambda r: r["frames"].append({"event": "x", "timestamp": 0.0, "url": "https://evil.example/a.jpg"}), "frames[0]"),
        # Invalid JPEG payload (valid base64 but not a JPEG)
        (lambda r: r["frames"].append(
            {"event": "bad", "timestamp": 0.0, "image_b64": base64.b64encode(b"not-a-jpeg").decode(),
             "image_mime": "image/jpeg"}
        ), "frames[0]"),
    ],
)
def test_invalid_receipts_return_structured_422_with_field_path(
    migrated_engine, api, mutate, expected_path_fragment
):
    job_id = _seed_job(migrated_engine, api.user_id, {"exercise_type": "auto"})
    result = valid_v1_result()
    mutate(result)
    resp = _post(api, job_id, result)
    assert resp.status_code == 422, resp.text
    body = resp.json()["error"]
    assert body["code"] == "MOTION_RESULT_SCHEMA_INVALID"
    assert body["retryable"] is False
    assert body["request_id"]
    field_path = body["details"]["field_path"] or ""
    assert expected_path_fragment in field_path, field_path


@pytest.mark.parametrize(
    "mutate",
    [
        # More than 4 preview frames (aggregate rule; path may be coarse)
        (lambda r: r["frames"].extend(
            [
                {"event": f"e{i}", "timestamp": float(i),
                 "image_b64": _tiny_jpeg_b64(), "image_mime": "image/jpeg"}
                for i in range(5)
            ]
        )),
        # Abstained (accepted=false) but still scored
        _score_while_abstained,
    ],
)
def test_invalid_receipts_return_structured_422(migrated_engine, api, mutate):
    job_id = _seed_job(migrated_engine, api.user_id, {"exercise_type": "auto"})
    result = valid_v1_result()
    mutate(result)
    resp = _post(api, job_id, result)
    assert resp.status_code == 422, resp.text
    body = resp.json()["error"]
    assert body["code"] == "MOTION_RESULT_SCHEMA_INVALID"
    assert body["retryable"] is False
    assert body["request_id"]
    assert set(body["details"].keys()) <= {"field_path"}


def test_field_path_loc_helper_formats_nested_paths():
    from pydantic import BaseModel, field_validator

    class Inner(BaseModel):
        v: int

        @field_validator("v")
        @classmethod
        def _bad(cls, value):
            raise ValueError("nope")

    class Outer(BaseModel):
        items: list[Inner]

    try:
        Outer.model_validate({"items": [{"v": 1}, {"v": 2}]})
    except Exception as exc:  # ValidationError
        path = motion_result_first_error_path(exc)
    assert path is not None and "items" in path


def test_422_body_never_echoes_payload(migrated_engine, api):
    job_id = _seed_job(migrated_engine, api.user_id, {"exercise_type": "auto"})
    secret = "SECRET-DO-NOT-LEAK-1234567890"
    result = valid_v1_result()
    result["frames"].append(
        {"event": "leak", "timestamp": 0.0, "image_b64": base64.b64encode(secret.encode()).decode(),
         "image_mime": "image/jpeg"}
    )
    resp = _post(api, job_id, result)
    assert resp.status_code == 422
    assert secret not in resp.text
