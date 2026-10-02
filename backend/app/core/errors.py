from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.errors import (
    STATUS_CODE_MAP,
    ApiException,
    error_body,
)
from app.schemas.worker import MotionResultSchemaError


class ErrorCode:
    """Legacy numeric codes, kept only for internal metrics/log correlation.

    The client contract is the string ``code`` in the unified envelope (spec
    §5.2); numeric codes are never returned to a client any more.
    """

    OK = 0
    BAD_REQUEST = 10001
    UNAUTHORIZED = 10002
    FORBIDDEN = 10003
    NOT_FOUND = 10004
    VALIDATION_ERROR = 10005
    CONFLICT = 10006
    PAYLOAD_TOO_LARGE = 10007
    INTERNAL_ERROR = 20000
    DATABASE_ERROR = 20001
    STORAGE_ERROR = 20002
    REDIS_ERROR = 20003
    AI_UNAVAILABLE = 30001
    MEDIA_PROCESSING_ERROR = 40001


def payload(code, message, data=None, request_id=None):
    """Deprecated internal helper; prefer ``error_body`` for client responses."""
    return {
        "code": int(code),
        "message": message,
        "data": data,
        "request_id": request_id,
    }


def register_exception_handlers(app):
    @app.exception_handler(ApiException)
    async def api_exc(request: Request, exc: ApiException):
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=exc.code,
                message=exc.user_message,
                request_id=rid,
                retryable=exc.retryable,
                details=exc.safe_details,
            ),
            headers=exc.headers,
        )

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        rid = getattr(request.state, "request_id", None)
        if isinstance(exc, ApiException):  # pragma: no cover - handler ordering
            return await api_exc(request, exc)  # type: ignore[misc]
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=STATUS_CODE_MAP.get(exc.status_code, "BAD_REQUEST"),
                message=message,
                request_id=rid,
                retryable=exc.status_code >= 500,
                details=exc.detail if isinstance(exc.detail, dict) else None,
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", None)
        # Only the field location survives; validator text may echo input values.
        first = exc.errors()[0] if exc.errors() else {}
        location = [str(part) for part in (first.get("loc") or []) if part != "body"]
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="VALIDATION_ERROR",
                message="提交的数据格式不正确",
                request_id=rid,
                details={"field_path": ".".join(location) or "body"},
            ),
        )

    @app.exception_handler(MotionResultSchemaError)
    async def motion_schema_exc(request: Request, exc: MotionResultSchemaError):
        # Worker receipt contract violations: permanent, non-retryable. The body
        # carries only a safe summary + the first failing field path. The raw
        # payload (possibly image base64) is never echoed.
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="MOTION_RESULT_SCHEMA_INVALID",
                message=exc.message,
                request_id=rid,
                details={"field_path": exc.field_path},
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_exc(request: Request, exc: SQLAlchemyError):
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content=error_body(
                code="DATABASE_ERROR",
                message="数据库操作失败，请稍后重试",
                request_id=rid,
                retryable=True,
            ),
        )

    @app.exception_handler(Exception)
    async def generic_exc(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content=error_body(
                code="INTERNAL_ERROR",
                message="服务器内部错误，请稍后重试",
                request_id=rid,
                retryable=True,
            ),
        )
