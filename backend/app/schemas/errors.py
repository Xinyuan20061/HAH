"""Unified API error envelope (spec §5.2).

Every business error that reaches a client uses exactly one shape::

    {"error": {"code", "message", "retryable", "request_id", "details"}}

Rules enforced here (and asserted by ``tests/test_api_contract_governance.py``):

* ``code`` is stable, upper-snake-case and branchable by the client;
* ``message`` is user-facing and never leaks provider text, SQL, paths or keys;
* ``details`` only carries whitelisted keys;
* 401 / 403 / 404 stay distinct, but a resource owned by another user is always
  404 so ids cannot be enumerated.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

# Status code -> stable machine code. Endpoint-specific codes override this.
STATUS_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    # Every 503 in this service is "a model or upstream capability is
    # unavailable"; keeping the historical stable code preserves client
    # branching while the envelope shape changes (spec §5.2).
    503: "AI_UNAVAILABLE",
}

# Only these detail keys may ever reach a client.
DETAIL_WHITELIST: frozenset[str] = frozenset(
    {
        "current_version",
        "expected_version",
        "field_path",
        "field",
        "limit",
        "max_limit",
        "analysis_id",
        "record_id",
        "proposal_id",
        "action_key",
        "status",
        "expires_at",
        "retry_after_seconds",
        "unsupported_schema_version",
        "supported_schema_versions",
        "required_confirmation",
    }
)


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(BaseModel):
    error: ApiErrorDetail


def safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Drop anything outside the whitelist; never echo raw upstream payloads."""
    if not details:
        return {}
    return {
        key: value
        for key, value in details.items()
        if key in DETAIL_WHITELIST
        and isinstance(value, (str, int, float, bool, list, type(None)))
    }


class ApiException(HTTPException):
    """HTTPException whose body is already the unified envelope.

    Raise this from endpoints so a specific, stable code and a whitelisted
    ``details`` payload reach the client instead of a generic status mapping.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.user_message = message
        self.retryable = retryable
        self.safe_details = safe_details(details)


def error_body(
    *,
    code: str,
    message: str,
    request_id: str | None,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ApiError(
        error=ApiErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            request_id=request_id or "",
            details=safe_details(details),
        )
    ).model_dump()
