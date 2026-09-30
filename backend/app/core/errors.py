from enum import IntEnum
from uuid import uuid4
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.worker import MotionResultSchemaError


class ErrorCode(IntEnum):
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


_STATUS_CODE_MAP = {
    503: ErrorCode.AI_UNAVAILABLE,
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
}


def payload(code, message, data=None, request_id=None):
    return {
        "code": int(code),
        "message": message,
        "data": data,
        "request_id": request_id,
    }


def register_exception_handlers(app):
    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        rid = getattr(request.state, "request_id", None)
        msg = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content=payload(
                _STATUS_CODE_MAP.get(exc.status_code, ErrorCode.BAD_REQUEST),
                msg,
                exc.detail if not isinstance(exc.detail, str) else None,
                rid,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", None)
        safe_errors = [
            {"loc": x["loc"], "type": x["type"], "msg": "字段格式或范围错误"}
            for x in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=payload(
                ErrorCode.VALIDATION_ERROR, "提交的数据格式不正确", safe_errors, rid
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
            content={
                "code": "MOTION_RESULT_SCHEMA_INVALID",
                "message": exc.message,
                "request_id": rid,
                "retryable": False,
                "details": {"field_path": exc.field_path},
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_exc(request: Request, exc: SQLAlchemyError):
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content=payload(ErrorCode.DATABASE_ERROR, "数据库操作失败", None, rid),
        )

    @app.exception_handler(Exception)
    async def generic_exc(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content=payload(ErrorCode.INTERNAL_ERROR, "服务器内部错误", None, rid),
        )
