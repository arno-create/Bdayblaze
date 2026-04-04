from __future__ import annotations

from datetime import datetime, timedelta

from bdayblaze.domain.birthday_logic import (
    current_celebration_window_utc,
    next_occurrence_after_current,
    next_occurrence_at_utc,
)
from bdayblaze.domain.models import BirthdayDisplayState


def resolve_birthday_display_state(
    *,
    birth_month: int,
    birth_day: int,
    timezone_name: str,
    scheduler_cursor_at_utc: datetime,
    now_utc: datetime,
    recovery_grace: timedelta,
    pending_occurrence_at_utc: datetime | None = None,
) -> BirthdayDisplayState:
    active_window = current_celebration_window_utc(
        birth_month=birth_month,
        birth_day=birth_day,
        timezone_name=timezone_name,
        now_utc=now_utc,
    )
    if active_window is not None:
        occurrence_start_at_utc, occurrence_end_at_utc = active_window
        return BirthdayDisplayState(
            status="active",
            relevant_occurrence_at_utc=occurrence_start_at_utc,
            next_future_occurrence_at_utc=next_occurrence_after_current(
                birth_month=birth_month,
                birth_day=birth_day,
                timezone_name=timezone_name,
                current_occurrence_at_utc=occurrence_start_at_utc,
            ),
            celebration_ends_at_utc=occurrence_end_at_utc,
        )

    recovering_occurrence_at_utc = _recovering_occurrence_at_utc(
        scheduler_cursor_at_utc=scheduler_cursor_at_utc,
        pending_occurrence_at_utc=pending_occurrence_at_utc,
        now_utc=now_utc,
        recovery_grace=recovery_grace,
    )
    if recovering_occurrence_at_utc is not None:
        return BirthdayDisplayState(
            status="recovering",
            relevant_occurrence_at_utc=recovering_occurrence_at_utc,
            next_future_occurrence_at_utc=next_occurrence_after_current(
                birth_month=birth_month,
                birth_day=birth_day,
                timezone_name=timezone_name,
                current_occurrence_at_utc=recovering_occurrence_at_utc,
            ),
        )

    return BirthdayDisplayState(
        status="upcoming",
        relevant_occurrence_at_utc=next_occurrence_at_utc(
            birth_month=birth_month,
            birth_day=birth_day,
            timezone_name=timezone_name,
            now_utc=now_utc,
        ),
        next_future_occurrence_at_utc=next_occurrence_at_utc(
            birth_month=birth_month,
            birth_day=birth_day,
            timezone_name=timezone_name,
            now_utc=now_utc,
        ),
    )


def _recovering_occurrence_at_utc(
    *,
    scheduler_cursor_at_utc: datetime,
    pending_occurrence_at_utc: datetime | None,
    now_utc: datetime,
    recovery_grace: timedelta,
) -> datetime | None:
    candidates = [
        occurrence_at_utc
        for occurrence_at_utc in (scheduler_cursor_at_utc, pending_occurrence_at_utc)
        if occurrence_at_utc is not None
        and occurrence_at_utc <= now_utc
        and now_utc - occurrence_at_utc <= recovery_grace
    ]
    if not candidates:
        return None
    return max(candidates)


def birthday_display_sort_key(
    display_state: BirthdayDisplayState,
) -> tuple[int, datetime]:
    rank = {
        "active": 0,
        "recovering": 1,
        "upcoming": 2,
    }[display_state.status]
    return rank, display_state.relevant_occurrence_at_utc
