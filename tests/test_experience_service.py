from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from bdayblaze.domain.models import (
    BirthdayCelebration,
    BirthdayWish,
    GuildExperienceSettings,
    GuildSettings,
    GuildSurpriseReward,
    MemberBirthday,
    TimelineEntry,
)
from bdayblaze.services.errors import NotFoundError, ValidationError
from bdayblaze.services.experience_service import ExperienceService


class FakeExperienceRepository:
    def __init__(self) -> None:
        self.experience_settings: GuildExperienceSettings | None = None
        self.guild_settings = replace(GuildSettings.default(1), default_timezone="UTC")
        self.birthday = MemberBirthday(
            guild_id=1,
            user_id=42,
            birth_month=3,
            birth_day=25,
            birth_year=2000,
            timezone_override=None,
            profile_visibility="private",
            next_occurrence_at_utc=datetime(2027, 3, 25, tzinfo=UTC),
            next_role_removal_at_utc=None,
            active_birthday_role_id=None,
        )
        self.saved_wish: BirthdayWish | None = None
        self.removed_wish: BirthdayWish | None = None
        self.current_celebration: BirthdayCelebration | None = None
        self.timeline_entries: list[TimelineEntry] = [
            TimelineEntry(
                celebration_id=1,
                occurrence_start_at_utc=datetime(2025, 3, 25, tzinfo=UTC),
                late_delivery=False,
                revealed_wish_count=4,
                quest_completed=True,
                featured_birthday=True,
                surprise_reward_type="badge",
                surprise_reward_label="Blaze badge",
                nitro_fulfillment_status=None,
            ),
            TimelineEntry(
                celebration_id=2,
                occurrence_start_at_utc=datetime(2024, 3, 25, tzinfo=UTC),
                late_delivery=True,
                revealed_wish_count=2,
                quest_completed=False,
                featured_birthday=False,
                surprise_reward_type=None,
                surprise_reward_label=None,
                nitro_fulfillment_status=None,
            ),
        ]
        self.rewards: list[GuildSurpriseReward] = []

    async def fetch_guild_experience_settings(
        self,
        guild_id: int,
    ) -> GuildExperienceSettings | None:
        return self.experience_settings

    async def upsert_guild_experience_settings(
        self,
        settings: GuildExperienceSettings,
    ) -> GuildExperienceSettings:
        self.experience_settings = settings
        return settings

    async def list_guild_surprise_rewards(self, guild_id: int) -> list[GuildSurpriseReward]:
        return list(self.rewards)

    async def upsert_guild_surprise_reward(
        self,
        reward: GuildSurpriseReward,
    ) -> GuildSurpriseReward:
        self.rewards = [
            existing
            for existing in self.rewards
            if existing.reward_type != reward.reward_type
        ]
        self.rewards.append(reward)
        return reward

    async def fetch_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        if user_id != self.birthday.user_id:
            return None
        return self.birthday

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        return self.guild_settings

    async def upsert_birthday_wish(self, **kwargs: object) -> BirthdayWish:
        self.saved_wish = BirthdayWish(
            id=1,
            guild_id=int(kwargs["guild_id"]),
            author_user_id=int(kwargs["author_user_id"]),
            target_user_id=int(kwargs["target_user_id"]),
            wish_text=str(kwargs["wish_text"]),
            link_url=kwargs["link_url"],  # type: ignore[assignment]
            state="queued",
            celebration_occurrence_at_utc=None,
            revealed_at_utc=None,
            removed_at_utc=None,
            moderated_by_user_id=None,
            created_at_utc=datetime(2026, 3, 1, tzinfo=UTC),
            updated_at_utc=datetime(2026, 3, 1, tzinfo=UTC),
        )
        return self.saved_wish

    async def list_queued_wishes_by_author(
        self,
        guild_id: int,
        author_user_id: int,
    ) -> list[BirthdayWish]:
        return [self.saved_wish] if self.saved_wish is not None else []

    async def remove_birthday_wish(self, **kwargs: object) -> BirthdayWish | None:
        return self.removed_wish

    async def list_birthday_wishes_for_target(
        self,
        guild_id: int,
        target_user_id: int,
        *,
        state: str,
        occurrence_start_at_utc: datetime | None = None,
    ) -> list[BirthdayWish]:
        if self.saved_wish is None:
            return []
        if state == self.saved_wish.state:
            return [self.saved_wish]
        return []

    async def fetch_birthday_celebration(
        self,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
    ) -> BirthdayCelebration | None:
        return self.current_celebration

    async def fetch_birthday_timeline_stats(
        self,
        guild_id: int,
        user_id: int,
    ) -> tuple[int, int, int, int]:
        return (2, 6, 1, 1)

    async def count_featured_birthdays(self, guild_id: int, user_id: int) -> int:
        return 1

    async def list_recent_birthday_celebrations(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int,
    ) -> list[TimelineEntry]:
        return self.timeline_entries[:limit]

    async def count_birthdays_for_day_visibility(
        self,
        guild_id: int,
        month: int,
        day: int,
        *,
        visible_only: bool,
    ) -> int:
        return 3 if visible_only else 5

    async def count_birthdays_for_month_visibility(
        self,
        guild_id: int,
        month: int,
        *,
        visible_only: bool,
    ) -> int:
        return 8 if visible_only else 11

    async def mark_birthday_quest_check_in(
        self,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
        *,
        checked_in_at_utc: datetime,
    ) -> BirthdayCelebration | None:
        if self.current_celebration is None:
            return None
        self.current_celebration = replace(
            self.current_celebration,
            quest_checked_in_at_utc=checked_in_at_utc,
            quest_completed_at_utc=checked_in_at_utc,
            featured_birthday=True,
        )
        return self.current_celebration

    async def list_pending_nitro_concierge(self, guild_id: int, *, limit: int) -> list[object]:
        return []

    async def fulfill_nitro_concierge(
        self,
        guild_id: int,
        celebration_id: int,
        *,
        admin_user_id: int,
        fulfillment_status: str,
    ) -> BirthdayCelebration | None:
        return None

    async def fetch_guild_analytics(self, guild_id: int, *, since_utc: datetime) -> object:
        raise AssertionError("Not used in these tests")


@pytest.mark.asyncio
async def test_add_wish_rejects_self_and_active_birthday_window() -> None:
    repository = FakeExperienceRepository()
    repository.experience_settings = replace(
        GuildExperienceSettings.default(1),
        capsules_enabled=True,
    )
    service = ExperienceService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="cannot wish yourself"):
        await service.add_wish(
            guild_id=1,
            author_user_id=42,
            target_user_id=42,
            wish_text="Happy birthday",
            link_url=None,
        )

    with pytest.raises(ValidationError, match="already opening for today"):
        await service.add_wish(
            guild_id=1,
            author_user_id=7,
            target_user_id=42,
            wish_text="Happy birthday",
            link_url=None,
            now_utc=datetime(2026, 3, 25, 12, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_add_wish_saves_safe_https_link() -> None:
    repository = FakeExperienceRepository()
    repository.experience_settings = replace(
        GuildExperienceSettings.default(1),
        capsules_enabled=True,
    )
    service = ExperienceService(repository)  # type: ignore[arg-type]

    wish = await service.add_wish(
        guild_id=1,
        author_user_id=7,
        target_user_id=42,
        wish_text="  Have the best day  ",
        link_url="https://example.com/card",
        now_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
    )

    assert wish.wish_text == "Have the best day"
    assert wish.link_url == "https://example.com/card"


@pytest.mark.asyncio
async def test_timeline_respects_private_visibility_without_admin_override() -> None:
    repository = FakeExperienceRepository()
    service = ExperienceService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="keeps their birthday private"):
        await service.build_timeline(
            guild_id=1,
            target_user_id=42,
            viewer_user_id=9,
            admin_override=False,
            now_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_build_timeline_includes_matching_counts_and_streak_for_self() -> None:
    repository = FakeExperienceRepository()
    service = ExperienceService(repository)  # type: ignore[arg-type]

    timeline = await service.build_timeline(
        guild_id=1,
        target_user_id=42,
        viewer_user_id=42,
        admin_override=False,
        now_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
    )

    assert timeline.celebration_streak == 2
    assert timeline.same_day_count == 2
    assert timeline.month_total_count == 7
    assert timeline.zodiac_label == "Aries"


@pytest.mark.asyncio
async def test_check_in_quest_marks_current_celebration_complete() -> None:
    repository = FakeExperienceRepository()
    repository.experience_settings = replace(
        GuildExperienceSettings.default(1),
        quests_enabled=True,
        quest_wish_target=3,
        quest_checkin_enabled=True,
    )
    repository.current_celebration = BirthdayCelebration(
        id=12,
        guild_id=1,
        user_id=42,
        occurrence_start_at_utc=datetime(2026, 3, 25, tzinfo=UTC),
        late_delivery=False,
        capsule_state="pending_public",
        capsule_message_id=None,
        revealed_wish_count=3,
        quest_enabled=True,
        quest_wish_target=3,
        quest_wish_goal_met=True,
        quest_checkin_required=True,
        quest_checked_in_at_utc=None,
        quest_completed_at_utc=None,
        featured_birthday=False,
        surprise_reward_type=None,
        surprise_reward_label=None,
        surprise_note_text=None,
        surprise_selected_at_utc=None,
        nitro_fulfillment_status=None,
        nitro_fulfilled_by_user_id=None,
        nitro_fulfilled_at_utc=None,
        created_at_utc=datetime(2026, 3, 25, tzinfo=UTC),
        updated_at_utc=datetime(2026, 3, 25, tzinfo=UTC),
    )
    service = ExperienceService(repository)  # type: ignore[arg-type]

    updated = await service.check_in_quest(
        guild_id=1,
        user_id=42,
        now_utc=datetime(2026, 3, 25, 12, tzinfo=UTC),
    )

    assert updated.quest_checked_in_at_utc is not None
    assert updated.quest_completed_at_utc is not None
    assert updated.featured_birthday is True


@pytest.mark.asyncio
async def test_fulfill_nitro_raises_for_missing_record() -> None:
    repository = FakeExperienceRepository()
    service = ExperienceService(repository)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError, match="No pending Nitro concierge"):
        await service.fulfill_nitro(
            1,
            99,
            admin_user_id=5,
            delivered=True,
        )
