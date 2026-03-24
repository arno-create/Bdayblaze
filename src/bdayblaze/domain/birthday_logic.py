from __future__ import annotations

from calendar import isleap
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone '{timezone_name}'. Use an IANA timezone like Europe/Berlin.") from exc


def validate_birth_date(month: int, day: int) -> None:
    try:
        date(2000, month, day)
    except ValueError as exc:
        raise ValueError("That month/day combination is not a valid birthday.") from exc


def normalize_birthday_for_year(month: int, day: int, target_year: int) -> date:
    if month == 2 and day == 29 and not isleap(target_year):
        return date(target_year, 2, 28)
    return date(target_year, month, day)


def local_midnight(target_date: date, timezone_name: str) -> datetime:
    zone = validate_timezone(timezone_name)
    return datetime.combine(target_date, time.min, tzinfo=zone)


def occurrence_local_date(occurrence_at_utc: datetime, timezone_name: str) -> date:
    zone = validate_timezone(timezone_name)
    return occurrence_at_utc.astimezone(zone).date()


def next_occurrence_at_utc(
    *,
    birth_month: int,
    birth_day: int,
    timezone_name: str,
    now_utc: datetime,
) -> datetime:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware.")
    zone = validate_timezone(timezone_name)
    local_now = now_utc.astimezone(zone)
    current_year_birthday = normalize_birthday_for_year(birth_month, birth_day, local_now.year)
    current_year_start = datetime.combine(current_year_birthday, time.min, tzinfo=zone)
    if current_year_start >= local_now:
        return current_year_start.astimezone(UTC)
    next_year_birthday = normalize_birthday_for_year(birth_month, birth_day, local_now.year + 1)
    return datetime.combine(next_year_birthday, time.min, tzinfo=zone).astimezone(UTC)


def next_occurrence_after_current(
    *,
    birth_month: int,
    birth_day: int,
    timezone_name: str,
    current_occurrence_at_utc: datetime,
) -> datetime:
    return next_occurrence_at_utc(
        birth_month=birth_month,
        birth_day=birth_day,
        timezone_name=timezone_name,
        now_utc=current_occurrence_at_utc + timedelta(seconds=1),
    )


def celebration_end_at_utc(start_at_utc: datetime, timezone_name: str) -> datetime:
    local_date = occurrence_local_date(start_at_utc, timezone_name)
    next_local_midnight = local_midnight(local_date + timedelta(days=1), timezone_name)
    return next_local_midnight.astimezone(UTC)


def compute_age(birth_year: int | None, celebration_date: date) -> int | None:
    if birth_year is None:
        return None
    return celebration_date.year - birth_year
