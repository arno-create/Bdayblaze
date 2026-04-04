from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bdayblaze.domain.birthday_display import resolve_birthday_display_state
from bdayblaze.domain.birthday_logic import (
    anniversary_month_day,
    celebration_end_at_utc,
    membership_age_days,
    next_occurrence_at_utc,
)
from bdayblaze.domain.models import GuildSettings, MemberBirthday
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import ValidationError


class FakeBirthdayRepository:
    def __init__(
        self,
        *,
        settings: GuildSettings | None = None,
        existing: MemberBirthday | None = None,
    ) -> None:
        self.settings = settings
        self.existing = existing
        self.saved: MemberBirthday | None = None

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        assert self.settings is None or self.settings.guild_id == guild_id
        return self.settings

    async def fetch_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        if (
            self.existing is not None
            and self.existing.guild_id == guild_id
            and self.existing.user_id == user_id
        ):
            return self.existing
        return None

    async def upsert_member_birthday(self, birthday: MemberBirthday) -> MemberBirthday:
        self.saved = birthday
        return birthday


def test_next_occurrence_handles_leap_day_on_non_leap_year() -> None:
    occurrence = next_occurrence_at_utc(
        birth_month=2,
        birth_day=29,
        timezone_name="UTC",
        now_utc=datetime(2025, 1, 15, tzinfo=UTC),
    )

    assert occurrence == datetime(2025, 2, 28, tzinfo=UTC)


def test_celebration_end_respects_dst_spring_forward() -> None:
    start = next_occurrence_at_utc(
        birth_month=3,
        birth_day=8,
        timezone_name="America/New_York",
        now_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    end = celebration_end_at_utc(start, "America/New_York")

    assert end - start == timedelta(hours=23)


def test_anniversary_month_day_uses_server_timezone() -> None:
    joined_at = datetime(2024, 3, 24, 21, 30, tzinfo=UTC)

    assert anniversary_month_day(joined_at, "Asia/Yerevan") == (3, 25)


def test_membership_age_days_returns_none_when_join_date_missing() -> None:
    assert membership_age_days(None, now_utc=datetime(2026, 3, 24, tzinfo=UTC)) is None


def test_birthday_display_state_is_active_during_local_birthday_window() -> None:
    display_state = resolve_birthday_display_state(
        birth_month=3,
        birth_day=24,
        timezone_name="UTC",
        scheduler_cursor_at_utc=datetime(2027, 3, 24, tzinfo=UTC),
        now_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
        recovery_grace=timedelta(hours=36),
    )

    assert display_state.status == "active"
    assert display_state.relevant_occurrence_at_utc == datetime(2026, 3, 24, tzinfo=UTC)


def test_birthday_display_state_uses_pending_occurrence_for_recovery_window() -> None:
    display_state = resolve_birthday_display_state(
        birth_month=3,
        birth_day=24,
        timezone_name="UTC",
        scheduler_cursor_at_utc=datetime(2027, 3, 24, tzinfo=UTC),
        now_utc=datetime(2026, 3, 25, 11, tzinfo=UTC),
        recovery_grace=timedelta(hours=36),
        pending_occurrence_at_utc=datetime(2026, 3, 24, tzinfo=UTC),
    )

    assert display_state.status == "recovering"
    assert display_state.relevant_occurrence_at_utc == datetime(2026, 3, 24, tzinfo=UTC)
    assert display_state.next_future_occurrence_at_utc == datetime(2027, 3, 24, tzinfo=UTC)


def test_birthday_display_state_handles_feb_29_normalization() -> None:
    display_state = resolve_birthday_display_state(
        birth_month=2,
        birth_day=29,
        timezone_name="UTC",
        scheduler_cursor_at_utc=datetime(2025, 2, 28, tzinfo=UTC),
        now_utc=datetime(2025, 1, 15, tzinfo=UTC),
        recovery_grace=timedelta(hours=36),
    )

    assert display_state.status == "upcoming"
    assert display_state.relevant_occurrence_at_utc == datetime(2025, 2, 28, tzinfo=UTC)


@pytest.mark.asyncio
async def test_birthday_service_uses_guild_default_timezone_when_override_missing() -> None:
    repository = FakeBirthdayRepository(settings=GuildSettings.default(1))
    service = BirthdayService(repository)  # type: ignore[arg-type]

    birthday = await service.set_birthday(
        guild_id=1,
        user_id=42,
        month=7,
        day=5,
        birth_year=None,
        timezone_override=None,
        now_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert birthday.timezone_override is None
    assert birthday.profile_visibility == "private"
    assert birthday.next_occurrence_at_utc == datetime(2026, 7, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_birthday_service_preserves_active_role_cleanup_on_timezone_change() -> None:
    existing = MemberBirthday(
        guild_id=1,
        user_id=42,
        birth_month=5,
        birth_day=20,
        birth_year=None,
        timezone_override="UTC",
        profile_visibility="private",
        next_occurrence_at_utc=datetime(2027, 5, 20, tzinfo=UTC),
        next_role_removal_at_utc=datetime(2026, 5, 21, tzinfo=UTC),
        active_birthday_role_id=999,
    )
    repository = FakeBirthdayRepository(settings=GuildSettings.default(1), existing=existing)
    service = BirthdayService(repository)  # type: ignore[arg-type]

    birthday = await service.set_birthday(
        guild_id=1,
        user_id=42,
        month=5,
        day=20,
        birth_year=None,
        timezone_override="Asia/Yerevan",
        profile_visibility="server_visible",
        now_utc=datetime(2026, 6, 1, tzinfo=UTC),
    )

    assert birthday.next_role_removal_at_utc == existing.next_role_removal_at_utc
    assert birthday.active_birthday_role_id == existing.active_birthday_role_id
    assert birthday.profile_visibility == "server_visible"
    assert birthday.timezone_override == "Asia/Yerevan"


@pytest.mark.asyncio
async def test_birthday_service_returns_clean_error_for_invalid_month_day() -> None:
    repository = FakeBirthdayRepository(settings=GuildSettings.default(1))
    service = BirthdayService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="valid birthday"):
        await service.set_birthday(
            guild_id=1,
            user_id=42,
            month=2,
            day=31,
            birth_year=None,
            timezone_override="UTC",
            now_utc=datetime(2026, 1, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_birthday_service_rejects_invalid_visibility() -> None:
    repository = FakeBirthdayRepository(settings=GuildSettings.default(1))
    service = BirthdayService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="Visibility must be"):
        await service.set_birthday(
            guild_id=1,
            user_id=42,
            month=3,
            day=24,
            birth_year=None,
            timezone_override="UTC",
            profile_visibility="global",  # type: ignore[arg-type]
            now_utc=datetime(2026, 1, 1, tzinfo=UTC),
        )
