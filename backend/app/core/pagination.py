"""Cursor pagination primitives (spec §5.3).

The cursor encodes only the non-sensitive ordering key ``(timestamp, id)`` as
base64url JSON. No offset arithmetic is used anywhere: a cursor is a position in
a totally ordered stream, so inserting a newer record cannot duplicate or skip a
row the way ``OFFSET`` does.

Tampered or truncated cursors raise ``InvalidCursor`` and are mapped to a 422
``INVALID_CURSOR`` envelope by the endpoint.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from app.core.time import naive_utc

CURSOR_VERSION = 1


class InvalidCursor(ValueError):
    pass


def encode_cursor(*, sort_value: datetime, row_id: int) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "t": naive_utc(sort_value).isoformat(),
        "i": int(row_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    if not cursor or len(cursor) > 512:
        raise InvalidCursor("cursor 为空或过长")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload: Any = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise InvalidCursor("cursor 不是合法的分页游标") from None
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise InvalidCursor("cursor 版本不受支持")
    try:
        sort_value = datetime.fromisoformat(str(payload["t"]))
        row_id = int(payload["i"])
    except (KeyError, TypeError, ValueError):
        raise InvalidCursor("cursor 缺少排序键") from None
    if row_id < 1:
        raise InvalidCursor("cursor 排序键无效")
    return naive_utc(sort_value), row_id


def clamp_limit(limit: int | None, *, default: int = 20, maximum: int = 50) -> int:
    if limit is None:
        return default
    if limit < 1 or limit > maximum:
        raise InvalidCursor(f"limit 必须在 1-{maximum} 之间")
    return limit
