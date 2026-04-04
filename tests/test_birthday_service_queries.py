from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bdayblaze.domain.models import BirthdayPreview, GuildSettings, MemberBirthday
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import NotFoundError


class FakeQueryRepository:
    def __init__(self) -> None:
        self.birthday: MemberBirthday | None = None
        self.guild_settings = GuildSettings.default(1)
        self.month_day_results: list[BirthdayPreview] = []
        self.month_results: list[BirthdayPreview] = []
        self.list_results: list[BirthdayPreview] = []
        self.upcoming_results: list[BirthdayPreview] = []
        self.pending_occurrences: dict[int, datetime] = {}
        self.requested_pairs: tuple[tuple[int, int], ...] | None = None
        self.last_visible_only: bool | None = None

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        assert guild_id == 1
        return self.guild_settings

    async def fetch_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        if (
            self.birthday is not None
            and self.birthday.guild_id == guild_id
            and self.birthday.user_id == user_id
        ):
            return self.birthday
        return None

    async def delete_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        if (
            self.birthday is not None
            and self.birthday.guild_id == guild_id
            and self.birthday.user_id == user_id
        ):
            deleted = self.birthday
            self.birthday = None
            return deleted
        return None

    async def list_birthdays_for_month_day_pairs(
        self,
        guild_id: int,
        month_day_pairs: tuple[tuple[int, int], ...],
        limit: int,
        *,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        self.requested_pairs = month_day_pairs
        self.last_visible_only = visible_only
        return self.month_day_results[:limit]

    async def fetch_pending_birthday_occurrences(
        self,
        guild_id: int,
        user_ids: list[int],
        *,
        since_utc: datetime,
    ) -> dict[int, datetime]:
        assert guild_id == 1
        return {
            user_id: occurrence_at_utc
            for user_id, occurrence_at_utc in self.pending_occurrences.items()
            if user_id in user_ids and occurrence_at_utc >= since_utc
        }

    async def list_birthdays_for_month(
        self,
        guild_id: int,
        month: int,
        limit: int,
        *,
        order_by_upcoming: bool,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        self.last_visible_only = visible_only
        return self.month_results[:limit]

    async def list_birthdays(
        self,
        guild_id: int,
        limit: int,
        *,
        order_by_upcoming: bool,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        self.last_visible_only = visible_only
        return self.list_results[:limit]

    async def list_upcoming_birthdays(
        self,
        guild_id: int,
        limit: int,
        *,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        self.last_visible_only = visible_only
        return self.upcoming_results[:limit]

    async def count_birthdays_by_day_for_month(
        self,
        guild_id: int,
        month: int,
        *,
        visible_only: bool,
        limit: int,
    ) -> list[tuple[int, int]]:
        self.last_visible_only = visible_only
        return [(24, 2), (25, 1)][:limit]


def _preview(
    *,
    user_id: int,
    month: int,
    day: int,
    timezone: str,
    visibility: str = "server_visible",
) -> BirthdayPreview:
    return BirthdayPreview(
        user_id=user_id,
        birth_month=month,
        birth_day=day,
        next_occurrence_at_utc=datetime(2027, month, min(day, 28), tzinfo=UTC),
        effective_timezone=timezone,
        profile_visibility=visibility,  # type: ignore[arg-type]
    )


def _birthday(*, user_id: int, month: int, day: int) -> MemberBirthday:
    return MemberBirthday(
        guild_id=1,
        user_id=user_id,
        birth_month=month,
        birth_day=day,
        birth_year=None,
        timezone_override="UTC",
        profile_visibility="private",
        next_occurrence_at_utc=datetime(2027, month, min(day, 28), tzinfo=UTC),
        next_role_removal_at_utc=None,
        active_birthday_role_id=None,
    )


@pytest.mark.asyncio
async def test_list_current_birthdays_filters_to_active_celebrations() -> None:
    repository = FakeQueryRepository()
    repository.month_day_results = [
        _preview(user_id=1, month=3, day=24, timezone="UTC"),
        _preview(user_id=2, month=3, day=25, timezone="UTC"),
    ]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    active = await service.list_current_birthdays(
        1,
        limit=10,
        visible_only=True,
        now_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
    )

    assert [preview.user_id for preview in active] == [1]
    assert repository.requested_pairs is not None
    assert (3, 24) in repository.requested_pairs
    assert repository.last_visible_only is True


@pytest.mark.asyncio
async def test_list_birthday_twins_excludes_the_requesting_member() -> None:
    repository = FakeQueryRepository()
    repository.birthday = _birthday(user_id=42, month=3, day=24)
    repository.month_day_results = [
        _preview(user_id=42, month=3, day=24, timezone="UTC"),
        _preview(user_id=77, month=3, day=24, timezone="UTC"),
    ]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    birthday, twins = await service.list_birthday_twins(
        1,
        42,
        limit=10,
        visible_only=True,
    )

    assert birthday.user_id == 42
    assert [preview.user_id for preview in twins] == [77]


@pytest.mark.asyncio
async def test_list_birthdays_for_month_returns_repository_results() -> None:
    repository = FakeQueryRepository()
    repository.month_results = [_preview(user_id=7, month=3, day=24, timezone="UTC")]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    results = await service.list_birthdays_for_month(
        1,
        month=3,
        limit=10,
        order_by_upcoming=False,
        visible_only=False,
    )

    assert [preview.user_id for preview in results] == [7]
    assert repository.last_visible_only is False


@pytest.mark.asyncio
async def test_list_birthdays_returns_repository_results() -> None:
    repository = FakeQueryRepository()
    repository.list_results = [_preview(user_id=8, month=4, day=1, timezone="UTC")]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    results = await service.list_birthdays(
        1,
        limit=10,
        order_by_upcoming=True,
        visible_only=True,
    )

    assert [preview.user_id for preview in results] == [8]
    assert repository.last_visible_only is True


@pytest.mark.asyncio
async def test_month_leaderboard_delegates_to_repository() -> None:
    repository = FakeQueryRepository()
    service = BirthdayService(repository)  # type: ignore[arg-type]

    leaderboard = await service.month_leaderboard(1, month=3, visible_only=False, limit=2)

    assert leaderboard == [(24, 2), (25, 1)]
    assert repository.last_visible_only is False


@pytest.mark.asyncio
async def test_require_birthday_uses_custom_missing_message() -> None:
    service = BirthdayService(FakeQueryRepository())  # type: ignore[arg-type]

    with pytest.raises(NotFoundError, match="admin view"):
        await service.require_birthday(1, 42, missing_message="Missing from admin view.")


@pytest.mark.asyncio
async def test_remove_member_birthday_uses_custom_missing_message() -> None:
    service = BirthdayService(FakeQueryRepository())  # type: ignore[arg-type]

    with pytest.raises(NotFoundError, match="admin remove"):
        await service.remove_member_birthday(1, 42, missing_message="Missing from admin remove.")


@pytest.mark.asyncio
async def test_list_browsable_birthdays_prioritizes_active_birthdays_over_future_cursor() -> None:
    repository = FakeQueryRepository()
    repository.upcoming_results = [
        _preview(user_id=7, month=4, day=1, timezone="UTC"),
    ]
    repository.month_day_results = [
        _preview(user_id=42, month=3, day=24, timezone="UTC"),
    ]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    entries = await service.list_browsable_birthdays(
        1,
        limit=10,
        order_by_upcoming=True,
        visible_only=True,
        now_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
    )

    assert [entry.preview.user_id for entry in entries] == [42, 7]
    assert [entry.display_state.status for entry in entries] == ["active", "upcoming"]


@pytest.mark.asyncio
async def test_list_browsable_birthdays_marks_recovering_occurrence_after_claim() -> None:
    repository = FakeQueryRepository()
    repository.upcoming_results = [
        _preview(user_id=7, month=4, day=1, timezone="UTC"),
    ]
    repository.month_day_results = [
        _preview(user_id=42, month=3, day=24, timezone="UTC"),
    ]
    repository.pending_occurrences[42] = datetime(2026, 3, 24, tzinfo=UTC)
    service = BirthdayService(repository)  # type: ignore[arg-type]

    entries = await service.list_browsable_birthdays(
        1,
        limit=10,
        order_by_upcoming=True,
        visible_only=True,
        now_utc=datetime(2026, 3, 25, 11, tzinfo=UTC),
    )

    assert entries[0].preview.user_id == 42
    assert entries[0].display_state.status == "recovering"


@pytest.mark.asyncio
async def test_list_browsable_birthdays_marks_unclaimed_overdue_cursor_as_recovering() -> None:
    repository = FakeQueryRepository()
    repository.upcoming_results = [
        _preview(user_id=7, month=4, day=1, timezone="UTC"),
    ]
    repository.month_day_results = [
        BirthdayPreview(
            user_id=42,
            birth_month=3,
            birth_day=24,
            next_occurrence_at_utc=datetime(2026, 3, 24, tzinfo=UTC),
            effective_timezone="UTC",
            profile_visibility="server_visible",
        )
    ]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    entries = await service.list_browsable_birthdays(
        1,
        limit=10,
        order_by_upcoming=True,
        visible_only=True,
        now_utc=datetime(2026, 3, 25, 6, tzinfo=UTC),
    )

    assert entries[0].preview.user_id == 42
    assert entries[0].display_state.status == "recovering"
