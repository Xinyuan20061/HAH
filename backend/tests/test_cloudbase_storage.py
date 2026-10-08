import json

import httpx

from app.core.config import settings
from app.services.cloudbase_storage import (
    CloudBaseStorageAdmin,
    CloudBaseStorageError,
    cloudbase_authorization,
)


def test_cloudbase_authorization_matches_published_signature_example():
    assert cloudbase_authorization(
        "AKIDDo-bNhLNl3kEY5HRzEG-CNUotmyFSadvpKimESWTfND98qyfrpYLCtQJ92_z9yN8",
        "wH72j2a5ZzhwgnXViwVNqdWhWn4AG4iasv26D4JdjBA=",
        1600227242,
    ) == (
        "1.0 TC3-HMAC-SHA256 "
        "Credential=AKIDDo-bNhLNl3kEY5HRzEG-CNUotmyFSadvpKimESWTfND98qyfrpYLCtQJ92_z9yN8/"
        "2020-09-16/tcb/tc3_request, SignedHeaders=content-type;host, "
        "Signature=0ce229810e251baa0ee2bb786c5f9eb6cb7758f55df28cbc161883c48a997e04"
    )


def test_cloudbase_delete_returns_a_trusted_result_per_file(monkeypatch):
    file_ok = "cloud://test-env/healthmate/u7/image/ok.jpg"
    file_denied = "cloud://test-env/healthmate/u7/image/denied.jpg"
    file_missing_receipt = "cloud://test-env/healthmate/u7/image/no-receipt.jpg"
    monkeypatch.setattr(settings, "cloudbase_env_id", "test-env")
    monkeypatch.setattr(settings, "cloudbase_storage_secret_id", "unit-test-id")
    monkeypatch.setattr(settings, "cloudbase_storage_secret_key", "unit-test-key")
    monkeypatch.setattr(settings, "cloudbase_storage_session_token", "unit-session")

    def handle(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.host == "tcb-api.tencentcloudapi.com"
        assert request.url.path.endswith("/envs/test-env/storages:batchDelete")
        assert request.headers["X-CloudBase-SessionToken"] == "unit-session"
        assert request.headers["X-CloudBase-Authorization"].startswith(
            "1.0 TC3-HMAC-SHA256 Credential=unit-test-id/"
        )
        assert json.loads(request.content) == {
            "data": {"fileList": [file_ok, file_denied, file_missing_receipt]}
        }
        return httpx.Response(
            200,
            json={
                "body": {
                    "data": {
                        "fileList": [
                            {"fileID": file_ok, "code": "SUCCESS"},
                            {"fileID": file_denied, "code": "DENIED"},
                        ]
                    }
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        result = CloudBaseStorageAdmin(client=client).delete_file_ids(
            [file_ok, file_denied, file_missing_receipt]
        )
    finally:
        client.close()
    assert result == {
        file_ok: True,
        file_denied: False,
        file_missing_receipt: False,
    }


def test_cloudbase_delete_fails_closed_when_admin_credentials_are_missing(monkeypatch):
    monkeypatch.setattr(settings, "cloudbase_env_id", "test-env")
    monkeypatch.setattr(settings, "cloudbase_storage_secret_id", "")
    monkeypatch.setattr(settings, "cloudbase_storage_secret_key", "")
    try:
        CloudBaseStorageAdmin().delete_file_ids(["cloud://test-env/a.jpg"])
    except CloudBaseStorageError as exc:
        assert "未配置" in str(exc)
    else:
        raise AssertionError("missing admin credentials must fail closed")
