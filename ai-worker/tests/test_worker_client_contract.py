# -*- coding: utf-8 -*-
"""P0-A: CloudAPIError carries safe server diagnostics and never retries
permanent 422/413, while 409 lease conflicts are flagged separately."""

import json

import httpx
import pytest

from healthmate_worker.client import CloudAPI, CloudAPIError
from healthmate_worker.config import settings

REAL_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "api_base_url", "https://cloud.example/api/v1")
    monkeypatch.setattr(settings, "worker_token", "test-token")
    monkeypatch.setattr(settings, "allow_private_media_hosts", False)


def _mock(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: REAL_CLIENT(transport=transport, **kw)
    )


def _schema_invalid_body():
    return {
        "code": "MOTION_RESULT_SCHEMA_INVALID",
        "message": "动作分析结果格式不符合约定",
        "request_id": "req-abc-123",
        "retryable": False,
        "details": {"field_path": "frames[2].image_mime"},
    }


def test_422_parses_code_field_path_request_id_and_is_not_retried(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(422, json=_schema_invalid_body())

    _mock(monkeypatch, handler)
    with CloudAPI() as api:
        with pytest.raises(CloudAPIError) as exc:
            api.heartbeat()
    err = exc.value
    assert err.status == 422
    assert err.code == "MOTION_RESULT_SCHEMA_INVALID"
    assert err.field_path == "frames[2].image_mime"
    assert err.request_id == "req-abc-123"
    assert err.retryable is False
    assert err.is_permanent_payload_error is True
    # Permanent: exactly one attempt, no retry loop.
    assert len(seen) == 1


def test_413_is_not_retried(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "api_max_retries", 3)

    def handler(request):
        seen.append(request)
        return httpx.Response(413, json={"code": "PAYLOAD_TOO_LARGE", "request_id": "req-9"})

    _mock(monkeypatch, handler)
    with CloudAPI() as api:
        with pytest.raises(CloudAPIError) as exc:
            api.heartbeat()
    assert exc.value.status == 413
    assert exc.value.retryable is False
    assert exc.value.request_id == "req-9"
    assert len(seen) == 1


def test_409_is_flagged_as_lease_conflict_and_not_retried(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(409, text="lease conflict")

    _mock(monkeypatch, handler)
    with CloudAPI() as api:
        with pytest.raises(CloudAPIError) as exc:
            api.heartbeat()
    assert exc.value.status == 409
    assert exc.value.is_lease_conflict is True
    assert exc.value.retryable is False
    assert len(seen) == 1


def test_error_does_not_echo_response_body_in_message(monkeypatch):
    secret = "secret-worker-token-in-body"

    def handler(request):
        return httpx.Response(422, json={**_schema_invalid_body(), "message": secret})

    _mock(monkeypatch, handler)
    with CloudAPI() as api:
        with pytest.raises(CloudAPIError) as exc:
            api.heartbeat()
    assert secret not in str(exc.value)
    # code/field_path/request_id are parsed as structured attributes.
    assert exc.value.code == "MOTION_RESULT_SCHEMA_INVALID"


def test_429_still_retries_bounded(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return (
            httpx.Response(429)
            if len(seen) < 2
            else httpx.Response(200, json={"ok": True})
        )

    _mock(monkeypatch, handler)
    monkeypatch.setattr("healthmate_worker.client.time.sleep", lambda _: None)
    with CloudAPI() as api:
        api.heartbeat()
    assert len(seen) == 2
