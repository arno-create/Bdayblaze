from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TypedDict

from bdayblaze.domain.birthday_display import resolve_birthday_display_state
from bdayblaze.domain.birthday_logic import (
    current_celebration_window_utc,
    is_birthday_active_now,
    occurrence_local_date,
    zodiac_sign,
)
from bdayblaze.domain.media_validation import validate_media_url_candidate
from bdayblaze.domain.models import (
    BirthdayCelebration,
    BirthdayQuestStatus,
    BirthdayTimeline,
    BirthdayWish,
    GuildAnalytics,
    GuildExperienceSettings,
    GuildSettings,
    GuildSurpriseReward,
    MemberBirthday,
    NitroConciergeEntry,
    SurpriseRewardType,
    TimelineEntry,
)
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.content_policy import ensure_safe_text
from bdayblaze.services.errors import NotFoundError, ValidationError

_REWARD_ORDER: tuple[SurpriseRewardType, ...] = (
    "featured",
    "badge",
    "custom_note",
    "nitro_concierge",
)
_DEFAULT_REWARD_LABELS: dict[SurpriseRewardType, str] = {
    "featured": "Featured birthday",
    "badge": "Blaze badge",
    "custom_note": "Birthday surprise",
    "nitro_concierge": "Nitro concierge",
}
_DEFAULT_REWARD_NOTES: dict[SurpriseRewardType, str | None] = {
    "featured": None,
    "badge": "Unlocked a Birthday Quest badge.",
    "custom_note": "A server host has a manual surprise queued for this birthday.",
    "nitro_concierge": (
        "Manual Nitro follow-up required. "
        "The bot never purchases or delivers Nitro."
    ),
}
_TIMELINE_ENTRY_LIMIT = 6
_TIMELINE_STREAK_SCAN_LIMIT = 24


class SurpriseRewardUpdate(TypedDict, total=False):
    enabled: bool
    weight: int
    label: str
    note_text: str | None


class _UnsetType:
    pass


UNSET = _UnsetType()


class ExperienceService:
    def __init__(
        self,
        repository: PostgresRepository,
        *,
        recovery_grace_hours: int = 36,
    ) -> None:
        self._repository = repository
        self._recovery_grace = timedelta(hours=recovery_grace_hours)

    async def get_settings(self, guild_id: int) -> GuildExperienceSettings:
        stored = await self._repository.fetch_guild_experience_settings(guild_id)
        return stored or GuildExperienceSettings.default(guild_id)

    async def update_settings(
        self,
        guild_id: int,
        *,
        capsules_enabled: bool | _UnsetType = UNSET,
        quests_enabled: bool | _UnsetType = UNSET,
        quest_wish_target: int | _UnsetType = UNSET,
        quest_reaction_target: int | _UnsetType = UNSET,
        quest_checkin_enabled: bool | _UnsetType = UNSET,
        surprises_enabled: bool | _UnsetType = UNSET,
    ) -> GuildExperienceSettings:
        current = await self.get_settings(guild_id)
        next_target = (
            current.quest_wish_target
            if isinstance(quest_wish_target, _UnsetType)
            else int(quest_wish_target)
        )
        if next_target < 1 or next_target > 25:
            raise ValidationError("Quest wish target must be between 1 and 25.")
        next_reaction_target = (
            current.quest_reaction_target
            if isinstance(quest_reaction_target, _UnsetType)
            else int(quest_reaction_target)
        )
        if next_reaction_target < 1 or next_reaction_target > 25:
            raise ValidationError("Quest reaction target must be between 1 and 25.")
        merged = replace(
            current,
            capsules_enabled=(
                current.capsules_enabled
                if isinstance(capsules_enabled, _UnsetType)
                else capsules_enabled
            ),
            quests_enabled=(
                current.quests_enabled if isinstance(quests_enabled, _UnsetType) else quests_enabled
            ),
            quest_wish_target=next_target,
            quest_reaction_target=next_reaction_target,
            quest_checkin_enabled=(
                current.quest_checkin_enabled
                if isinstance(quest_checkin_enabled, _UnsetType)
                else quest_checkin_enabled
            ),
            surprises_enabled=(
                current.surprises_enabled
                if isinstance(surprises_enabled, _UnsetType)
                else surprises_enabled
            ),
        )
        return await self._repository.upsert_guild_experience_settings(merged)

    async def list_surprise_rewards(self, guild_id: int) -> list[GuildSurpriseReward]:
        stored = {
            reward.reward_type: reward
            for reward in await self._repository.list_guild_surprise_rewards(guild_id)
        }
        return [
            stored.get(reward_type, _default_surprise_reward(guild_id, reward_type))
            for reward_type in _REWARD_ORDER
        ]

    async def upsert_surprise_reward(
        self,
        guild_id: int,
        reward_type: SurpriseRewardType,
        *,
        enabled: bool | None = None,
        weight: int | None = None,
        label: str | None = None,
        note_text: str | None | _UnsetType = UNSET,
    ) -> GuildSurpriseReward:
        current = {
            reward.reward_type: reward
            for reward in await self.list_surprise_rewards(guild_id)
        }[reward_type]
        next_weight = current.weight if weight is None else int(weight)
        if next_weight < 0 or next_weight > 1000:
            raise ValidationError("Surprise weight must be between 0 and 1000.")
        next_label = _normalize_reward_label(
            label if label is not None else current.label,
            reward_type=reward_type,
        )
        next_note = _normalize_reward_note(
            current.note_text if isinstance(note_text, _UnsetType) else note_text,
            reward_type=reward_type,
        )
        reward = replace(
            current,
            enabled=current.enabled if enabled is None else enabled,
            weight=next_weight,
            label=next_label,
            note_text=next_note,
        )
        return await self._repository.upsert_guild_surprise_reward(reward)

    async def update_surprise_rewards(
        self,
        guild_id: int,
        *,
        updates: dict[SurpriseRewardType, SurpriseRewardUpdate],
    ) -> list[GuildSurpriseReward]:
        current_rewards = {
            reward.reward_type: reward for reward in await self.list_surprise_rewards(guild_id)
        }
        next_rewards: list[GuildSurpriseReward] = []
        for reward_type in _REWARD_ORDER:
            current = current_rewards[reward_type]
            patch = updates.get(reward_type, {})
            enabled_value = patch.get("enabled")
            weight_value = patch.get("weight")
            label_value = patch.get("label")
            note_marker = patch.get("note_text", UNSET)
            next_weight = current.weight if weight_value is None else int(weight_value)
            if next_weight < 0 or next_weight > 1000:
                raise ValidationError("Surprise weight must be between 0 and 1000.")
            next_label = _normalize_reward_label(
                current.label if label_value is None else str(label_value),
                reward_type=reward_type,
            )
            next_note = _normalize_reward_note(
                current.note_text if isinstance(note_marker, _UnsetType) else note_marker,
                reward_type=reward_type,
            )
            next_rewards.append(
                replace(
                    current,
                    enabled=current.enabled if enabled_value is None else bool(enabled_value),
                    weight=next_weight,
                    label=next_label,
                    note_text=next_note,
                )
            )
        return await self._repository.upsert_guild_surprise_rewards(next_rewards)

    async def add_wish(
        self,
        *,
        guild_id: int,
        author_user_id: int,
        target_user_id: int,
        wish_text: str,
        link_url: str | None,
        max_wish_length: int = 350,
        now_utc: datetime | None = None,
    ) -> BirthdayWish:
        if author_user_id == target_user_id:
            raise ValidationError(
                "Birthday Capsules are for other members. You cannot wish yourself."
            )
        settings = await self.get_settings(guild_id)
        if not settings.capsules_enabled:
            raise ValidationError("Birthday Capsules are disabled in this server.")
        birthday, guild_settings = await self._require_birthday_context(guild_id, target_user_id)
        effective_now = now_utc or datetime.now(UTC)
        if is_birthday_active_now(
            birth_month=birthday.birth_month,
            birth_day=birthday.birth_day,
            timezone_name=birthday.effective_timezone(guild_settings),
            now_utc=effective_now,
        ):
            raise ValidationError(
                "That capsule is already opening for today. "
                "New wishes lock once the birthday starts."
            )
        normalized_text = wish_text.strip()
        if not normalized_text:
            raise ValidationError("Birthday wish text cannot be blank.")
        if len(normalized_text) > max_wish_length:
            raise ValidationError(
                f"Birthday wish text must be {max_wish_length} characters or fewer."
            )
        ensure_safe_text(normalized_text, label="Birthday wish")
        normalized_link = None
        if link_url is not None and link_url.strip():
            try:
                normalized_link = validate_media_url_candidate(
                    link_url.strip(),
                    label="Wish link",
                    allow_validated_marker=False,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        return await self._repository.upsert_birthday_wish(
            guild_id=guild_id,
            author_user_id=author_user_id,
            target_user_id=target_user_id,
            wish_text=normalized_text,
            link_url=normalized_link,
        )

    async def list_author_wishes(self, guild_id: int, author_user_id: int) -> list[BirthdayWish]:
        return await self._repository.list_queued_wishes_by_author(guild_id, author_user_id)

    async def remove_wish(
        self,
        *,
        guild_id: int,
        actor_user_id: int,
        target_user_id: int,
        author_user_id: int | None = None,
        moderated: bool = False,
    ) -> BirthdayWish:
        removed = await self._repository.remove_birthday_wish(
            guild_id=guild_id,
            author_user_id=actor_user_id if author_user_id is None else author_user_id,
            target_user_id=target_user_id,
            moderator_user_id=actor_user_id if moderated else None,
            moderated=moderated,
        )
        if removed is None:
            raise NotFoundError("No unrevealed birthday wish matched that request.")
        return removed

    async def list_capsule_preview(
        self,
        *,
        guild_id: int,
        target_user_id: int,
        include_private_queued: bool,
        now_utc: datetime | None = None,
    ) -> tuple[BirthdayCelebration | None, list[BirthdayWish], int]:
        current = await self._current_celebration(guild_id, target_user_id, now_utc=now_utc)
        if current is not None and current.revealed_wish_count > 0:
            revealed = await self._repository.list_birthday_wishes_for_target(
                guild_id,
                target_user_id,
                state="revealed",
                occurrence_start_at_utc=current.occurrence_start_at_utc,
            )
            return current, revealed, 0
        queued = await self._repository.list_birthday_wishes_for_target(
            guild_id,
            target_user_id,
            state="queued",
        )
        return current, (queued if include_private_queued else []), len(queued)

    async def get_quest_status(
        self,
        guild_id: int,
        user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> BirthdayQuestStatus:
        settings = await self.get_settings(guild_id)
        celebration = await self._current_celebration(guild_id, user_id, now_utc=now_utc)
        can_check_in = bool(
            celebration is not None
            and celebration.quest_enabled
            and celebration.quest_checkin_required
            and celebration.quest_checked_in_at_utc is None
        )
        return BirthdayQuestStatus(
            celebration=celebration,
            settings=settings,
            can_check_in=can_check_in,
        )

    async def has_tracked_birthday_announcement_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> bool:
        return await self._repository.has_tracked_birthday_announcement_message(
            guild_id,
            message_id,
        )

    async def fetch_announcement_channel_for_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> int | None:
        return await self._repository.fetch_announcement_channel_for_message(
            guild_id,
            message_id,
        )

    async def refresh_birthday_announcement_reactions(
        self,
        guild_id: int,
        message_id: int,
        reaction_count: int,
    ) -> list[BirthdayCelebration]:
        return await self._repository.refresh_birthday_announcement_reactions(
            guild_id,
            message_id,
            reaction_count,
        )

    async def disable_birthday_announcement_reaction_tracking(
        self,
        guild_id: int,
        message_id: int,
    ) -> list[BirthdayCelebration]:
        return await self._repository.disable_birthday_announcement_reaction_tracking(
            guild_id,
            message_id,
        )

    async def check_in_quest(
        self,
        guild_id: int,
        user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> BirthdayCelebration:
        status = await self.get_quest_status(guild_id, user_id, now_utc=now_utc)
        if not status.settings.quests_enabled:
            raise ValidationError("Birthday Quests are disabled in this server.")
        if status.celebration is None:
            raise ValidationError("Quest check-in only works during your active birthday window.")
        if not status.celebration.quest_enabled:
            raise ValidationError("Birthday Quests were not enabled for this celebration.")
        if not status.celebration.quest_checkin_required:
            raise ValidationError("This birthday quest does not require a check-in.")
        if status.celebration.quest_checked_in_at_utc is not None:
            return status.celebration
        updated = await self._repository.mark_birthday_quest_check_in(
            guild_id,
            user_id,
            status.celebration.occurrence_start_at_utc,
            checked_in_at_utc=now_utc or datetime.now(UTC),
        )
        if updated is None:
            raise ValidationError("That birthday quest is no longer available for check-in.")
        return updated

    async def build_timeline(
        self,
        *,
        guild_id: int,
        target_user_id: int,
        viewer_user_id: int,
        admin_override: bool,
        history_entry_limit: int = _TIMELINE_ENTRY_LIMIT,
        now_utc: datetime | None = None,
    ) -> BirthdayTimeline:
        birthday, guild_settings = await self._require_birthday_context(guild_id, target_user_id)
        if (
            target_user_id != viewer_user_id
            and not admin_override
            and birthday.profile_visibility != "server_visible"
        ):
            raise ValidationError("That member keeps their birthday private in this server.")
        effective_now = now_utc or datetime.now(UTC)
        pending_occurrences = await self._repository.fetch_pending_birthday_occurrences(
            guild_id,
            [target_user_id],
            since_utc=effective_now - self._recovery_grace,
        )
        display_state = resolve_birthday_display_state(
            birth_month=birthday.birth_month,
            birth_day=birthday.birth_day,
            timezone_name=birthday.effective_timezone(guild_settings),
            scheduler_cursor_at_utc=birthday.next_occurrence_at_utc,
            now_utc=effective_now,
            recovery_grace=self._recovery_grace,
            pending_occurrence_at_utc=pending_occurrences.get(target_user_id),
        )
        active_celebration = await self._current_celebration(
            guild_id,
            target_user_id,
            now_utc=effective_now,
        )
        if active_celebration is None and display_state.status == "recovering":
            active_celebration = await self._repository.fetch_birthday_celebration(
                guild_id,
                target_user_id,
                display_state.relevant_occurrence_at_utc,
            )
        celebration_count, wishes_received_count, quest_badge_count, surprise_count = (
            await self._repository.fetch_birthday_timeline_stats(guild_id, target_user_id)
        )
        featured_count = await self._repository.count_featured_birthdays(guild_id, target_user_id)
        entries = await self._repository.list_recent_birthday_celebrations(
            guild_id,
            target_user_id,
            limit=history_entry_limit,
        )
        streak_scan = await self._repository.list_recent_birthday_celebrations(
            guild_id,
            target_user_id,
            limit=_TIMELINE_STREAK_SCAN_LIMIT,
        )
        visible_only = not admin_override
        same_day_count = await self._repository.count_birthdays_for_day_visibility(
            guild_id,
            birthday.birth_month,
            birthday.birth_day,
            visible_only=visible_only,
        )
        month_total_count = await self._repository.count_birthdays_for_month_visibility(
            guild_id,
            birthday.birth_month,
            visible_only=visible_only,
        )
        show_zodiac = admin_override or target_user_id == viewer_user_id
        return BirthdayTimeline(
            birthday=birthday,
            active_celebration=active_celebration,
            display_state=display_state,
            celebration_count=celebration_count,
            celebration_streak=_celebration_streak(
                streak_scan,
                timezone_name=birthday.effective_timezone(guild_settings),
            ),
            wishes_received_count=wishes_received_count,
            quest_badge_count=quest_badge_count,
            surprise_count=surprise_count,
            featured_count=featured_count,
            next_countdown_at_utc=display_state.relevant_occurrence_at_utc,
            same_day_count=max(0, same_day_count - 1),
            month_total_count=max(0, month_total_count - 1),
            zodiac_label=(
                zodiac_sign(birthday.birth_month, birthday.birth_day) if show_zodiac else None
            ),
            entries=tuple(entries),
        )

    async def fetch_analytics(
        self,
        guild_id: int,
        *,
        since_utc: datetime | None = None,
    ) -> GuildAnalytics:
        return await self._repository.fetch_guild_analytics(
            guild_id,
            since_utc=since_utc or datetime.now(UTC) - timedelta(days=30),
        )

    async def list_pending_nitro(
        self,
        guild_id: int,
        *,
        limit: int = 10,
    ) -> list[NitroConciergeEntry]:
        return await self._repository.list_pending_nitro_concierge(guild_id, limit=limit)

    async def fulfill_nitro(
        self,
        guild_id: int,
        celebration_id: int,
        *,
        admin_user_id: int,
        delivered: bool,
    ) -> BirthdayCelebration:
        celebration = await self._repository.fulfill_nitro_concierge(
            guild_id,
            celebration_id,
            admin_user_id=admin_user_id,
            fulfillment_status="delivered" if delivered else "not_delivered",
        )
        if celebration is None:
            raise NotFoundError("No pending Nitro concierge record matched that celebration.")
        return celebration

    async def _require_birthday_context(
        self,
        guild_id: int,
        user_id: int,
    ) -> tuple[MemberBirthday, GuildSettings]:
        birthday = await self._repository.fetch_member_birthday(guild_id, user_id)
        if birthday is None:
            raise NotFoundError("That member does not have a saved birthday in this server.")
        guild_settings = await self._repository.fetch_guild_settings(guild_id)
        return birthday, guild_settings or GuildSettings.default(guild_id)

    async def _current_celebration(
        self,
        guild_id: int,
        user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> BirthdayCelebration | None:
        birthday, guild_settings = await self._require_birthday_context(guild_id, user_id)
        window = current_celebration_window_utc(
            birth_month=birthday.birth_month,
            birth_day=birthday.birth_day,
            timezone_name=birthday.effective_timezone(guild_settings),
            now_utc=now_utc or datetime.now(UTC),
        )
        if window is None:
            return None
        return await self._repository.fetch_birthday_celebration(
            guild_id,
            user_id,
            window[0],
        )


def _default_surprise_reward(
    guild_id: int,
    reward_type: SurpriseRewardType,
) -> GuildSurpriseReward:
    return GuildSurpriseReward(
        id=None,
        guild_id=guild_id,
        reward_type=reward_type,
        label=_DEFAULT_REWARD_LABELS[reward_type],
        weight=0,
        enabled=False,
        note_text=_DEFAULT_REWARD_NOTES[reward_type],
    )


def _normalize_reward_label(value: str, *, reward_type: SurpriseRewardType) -> str:
    normalized = value.strip()
    if not normalized:
        normalized = _DEFAULT_REWARD_LABELS[reward_type]
    if len(normalized) > 80:
        raise ValidationError("Surprise label must be 80 characters or fewer.")
    ensure_safe_text(normalized, label=f"{reward_type.replace('_', ' ').title()} reward label")
    return normalized


def _normalize_reward_note(
    value: str | None,
    *,
    reward_type: SurpriseRewardType,
) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 200:
        raise ValidationError("Surprise note must be 200 characters or fewer.")
    ensure_safe_text(normalized, label=f"{reward_type.replace('_', ' ').title()} reward note")
    return normalized


def _celebration_streak(
    entries: list[TimelineEntry],
    *,
    timezone_name: str,
) -> int:
    if not entries:
        return 0
    years = [
        occurrence_local_date(entry.occurrence_start_at_utc, timezone_name).year
        for entry in entries
    ]
    streak = 1
    for previous, current in pairwise(years):
        if previous - current != 1:
            break
        streak += 1
    return streak
