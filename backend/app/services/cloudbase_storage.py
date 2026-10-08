"""Server-side CloudBase Storage administration via the official Open API.

Credentials are loaded only from backend settings. The request signature follows
CloudBase's documented TC3 credential format and never includes user data in logs.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings


_HOST = "tcb-api.tencentcloudapi.com"
_BASE_URL = f"https://{_HOST}"
_CANONICAL_REQUEST = (
    "POST\n//api.tcloudbase.com/\n\n"
    "content-type:application/json; charset=utf-8\n"
    "host:api.tcloudbase.com\n\n"
    "content-type;host\n"
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
_BATCH_SIZE = 20


class CloudBaseStorageError(RuntimeError):
    """A sanitized provider error; never carries request credentials or file IDs."""


def cloudbase_authorization(secret_id: str, secret_key: str, timestamp: int) -> str:
    """Build the documented CloudBase Open API authorization header."""
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    algorithm = "TC3-HMAC-SHA256"
    service = "tcb"
    scope = f"{date}/{service}/tc3_request"
    hashed_request = hashlib.sha256(_CANONICAL_REQUEST.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{scope}\n{hashed_request}"

    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    secret_date = sign(f"TC3{secret_key}".encode("utf-8"), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"1.0 {algorithm} Credential={secret_id}/{scope}, "
        f"SignedHeaders=content-type;host, Signature={signature}"
    )


class CloudBaseStorageAdmin:
    def __init__(self, *, client: httpx.Client | None = None):
        self.env_id = settings.cloudbase_env_id.strip()
        self.secret_id = settings.cloudbase_storage_secret_id.strip()
        self.secret_key = settings.cloudbase_storage_secret_key
        self.session_token = settings.cloudbase_storage_session_token.strip()
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.env_id and self.secret_id and self.secret_key.strip())

    def delete_file_ids(self, file_ids: list[str]) -> dict[str, bool]:
        """Delete stable CloudBase file IDs and return a server receipt per ID."""
        if not self.configured:
            raise CloudBaseStorageError("CloudBase 管理端存储凭据未配置")
        unique_ids = list(dict.fromkeys(item.strip() for item in file_ids if item.strip()))
        results: dict[str, bool] = {}
        for start in range(0, len(unique_ids), _BATCH_SIZE):
            batch = unique_ids[start : start + _BATCH_SIZE]
            results.update(self._delete_batch(batch))
        return results

    def _delete_batch(self, file_ids: list[str]) -> dict[str, bool]:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        headers = {
            "X-CloudBase-Authorization": cloudbase_authorization(
                self.secret_id, self.secret_key, timestamp
            ),
            "X-CloudBase-TimeStamp": str(timestamp),
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.session_token:
            headers["X-CloudBase-SessionToken"] = self.session_token
        url = f"{_BASE_URL}/api/v2/envs/{quote(self.env_id, safe='')}/storages:batchDelete"
        client = self._client or httpx.Client(timeout=20.0)
        close_client = self._client is None
        try:
            response = client.post(
                url,
                headers=headers,
                json={"data": {"fileList": file_ids}},
                timeout=20.0,
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CloudBaseStorageError(
                f"CloudBase 文件删除请求失败（{type(exc).__name__}）"
            ) from None
        finally:
            if close_client:
                client.close()

        body = payload.get("body", payload) if isinstance(payload, dict) else None
        if not isinstance(body, dict) or body.get("code"):
            raise CloudBaseStorageError("CloudBase 文件删除接口返回错误")
        data = body.get("data") or {}
        items = data.get("fileList") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise CloudBaseStorageError("CloudBase 文件删除回执格式无效")
        returned = {
            item.get("fileID"): item.get("code") == "SUCCESS"
            for item in items
            if isinstance(item, dict) and isinstance(item.get("fileID"), str)
        }
        return {file_id: returned.get(file_id, False) for file_id in file_ids}
