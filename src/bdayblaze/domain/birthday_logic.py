from __future__ import annotations

from calendar import isleap
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bdayblaze.domain.timezones import timezone_examples_text


def validate_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Unknown timezone '{timezone_name}'. Use an IANA timezone like "
            f"{timezone_examples_text()}."
        ) from exc


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


def current_celebration_window_utc(
    *,
    birth_month: int,
    birth_day: int,
    timezone_name: str,
    now_utc: datetime,
) -> tuple[datetime, datetime] | None:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware.")
    zone = validate_timezone(timezone_name)
    local_now = now_utc.astimezone(zone)
    local_birthday = normalize_birthday_for_year(birth_month, birth_day, local_now.year)
    if local_birthday != local_now.date():
        return None
    start_at_utc = datetime.combine(local_birthday, time.min, tzinfo=zone).astimezone(UTC)
    return start_at_utc, celebration_end_at_utc(start_at_utc, timezone_name)


def is_birthday_active_now(
    *,
    birth_month: int,
    birth_day: int,
    timezone_name: str,
    now_utc: datetime,
) -> bool:
    window = current_celebration_window_utc(
        birth_month=birth_month,
        birth_day=birth_day,
        timezone_name=timezone_name,
        now_utc=now_utc,
    )
    if window is None:
        return False
    start_at_utc, end_at_utc = window
    return start_at_utc <= now_utc < end_at_utc


def active_window_candidate_birthdays(now_utc: datetime) -> tuple[tuple[int, int], ...]:
    candidate_pairs: set[tuple[int, int]] = set()
    for offset_days in (-1, 0, 1):
        candidate_date = (now_utc + timedelta(days=offset_days)).date()
        candidate_pairs.add((candidate_date.month, candidate_date.day))
        if candidate_date.month == 2 and candidate_date.day == 28 and not isleap(
            candidate_date.year
        ):
            candidate_pairs.add((2, 29))
    return tuple(sorted(candidate_pairs))


def compute_age(birth_year: int | None, celebration_date: date) -> int | None:
    if birth_year is None:
        return None
    return celebration_date.year - birth_year
