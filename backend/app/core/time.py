"""Persist naive UTC in existing DATETIME columns; serialize explicit UTC at API boundaries."""

from datetime import datetime, timezone, date, time, timedelta
from typing import Annotated
from pydantic import AfterValidator, PlainSerializer


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def utc_iso(value: datetime | None) -> str | None:
    return (
        naive_utc(value).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        if value
        else None
    )


UTCDateTime = Annotated[
    datetime,
    AfterValidator(naive_utc),
    PlainSerializer(utc_iso, return_type=str, when_used="json"),
]

BUSINESS_TZ = timezone(timedelta(hours=8))


def business_today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def business_day(value: datetime) -> date:
    return naive_utc(value).replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ).date()


def utc_day_bounds(value: date) -> tuple[datetime, datetime]:
    return (
        naive_utc(datetime.combine(value, time.min, tzinfo=BUSINESS_TZ)),
        naive_utc(datetime.combine(value, time.max, tzinfo=BUSINESS_TZ)),
    )
