from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import asyncpg

from bdayblaze.domain.announcement_surfaces import resolve_announcement_surface
from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNIVERSARY_TEMPLATE,
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    DEFAULT_DM_TEMPLATE,
)
from bdayblaze.domain.birthday_logic import (
    anniversary_month_day,
    celebration_end_at_utc,
    next_occurrence_after_current,
    next_occurrence_at_utc,
)
from bdayblaze.domain.models import (
    AnnouncementBatch,
    AnnouncementBatchClaim,
    AnnouncementSurfaceKind,
    AnnouncementSurfaceSettings,
    BirthdayCelebration,
    BirthdayPreview,
    BirthdayWish,
    CelebrationEvent,
    GuildAnalytics,
    GuildExperienceSettings,
    GuildSettings,
    GuildSurpriseReward,
    MemberBirthday,
    NitroConciergeEntry,
    RecentDeliveryIssue,
    RecurringCelebration,
    SchedulerBacklog,
    TimelineEntry,
    TrackedAnniversary,
)


class PostgresRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM guild_settings WHERE guild_id = $1",
                guild_id,
            )
        return self._map_guild_settings(row) if row is not None else None

    async def upsert_guild_settings(self, settings: GuildSettings) -> GuildSettings:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    default_timezone,
                    birthday_role_id,
                    announcements_enabled,
                    role_enabled,
                    celebration_mode,
                    announcement_theme,
                    announcement_template,
                    announcement_title_override,
                    announcement_footer_text,
                    announcement_accent_color,
                    birthday_dm_enabled,
                    birthday_dm_template,
                    anniversary_enabled,
                    anniversary_template,
                    eligibility_role_id,
                    ignore_bots,
                    minimum_membership_days,
                    mention_suppression_threshold,
                    studio_audit_channel_id,
                    updated_at_utc
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, NOW()
                )
                ON CONFLICT (guild_id) DO UPDATE SET
                    default_timezone = EXCLUDED.default_timezone,
                    birthday_role_id = EXCLUDED.birthday_role_id,
                    announcements_enabled = EXCLUDED.announcements_enabled,
                    role_enabled = EXCLUDED.role_enabled,
                    celebration_mode = EXCLUDED.celebration_mode,
                    announcement_theme = EXCLUDED.announcement_theme,
                    announcement_template = EXCLUDED.announcement_template,
                    announcement_title_override = EXCLUDED.announcement_title_override,
                    announcement_footer_text = EXCLUDED.announcement_footer_text,
                    announcement_accent_color = EXCLUDED.announcement_accent_color,
                    birthday_dm_enabled = EXCLUDED.birthday_dm_enabled,
                    birthday_dm_template = EXCLUDED.birthday_dm_template,
                    anniversary_enabled = EXCLUDED.anniversary_enabled,
                    anniversary_template = EXCLUDED.anniversary_template,
                    eligibility_role_id = EXCLUDED.eligibility_role_id,
                    ignore_bots = EXCLUDED.ignore_bots,
                    minimum_membership_days = EXCLUDED.minimum_membership_days,
                    mention_suppression_threshold = EXCLUDED.mention_suppression_threshold,
                    studio_audit_channel_id = EXCLUDED.studio_audit_channel_id,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                settings.guild_id,
                settings.default_timezone,
                settings.birthday_role_id,
                settings.announcements_enabled,
                settings.role_enabled,
                settings.celebration_mode,
                settings.announcement_theme,
                settings.announcement_template,
                settings.announcement_title_override,
                settings.announcement_footer_text,
                settings.announcement_accent_color,
                settings.birthday_dm_enabled,
                settings.birthday_dm_template,
                settings.anniversary_enabled,
                settings.anniversary_template,
                settings.eligibility_role_id,
                settings.ignore_bots,
                settings.minimum_membership_days,
                settings.mention_suppression_threshold,
                settings.studio_audit_channel_id,
            )
        return self._map_guild_settings(row)

    async def list_guild_announcement_surfaces(
        self,
        guild_id: int,
    ) -> dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM guild_announcement_surfaces
                WHERE guild_id = $1
                """,
                guild_id,
            )
        return {
            surface.surface_kind: surface
            for surface in (self._map_announcement_surface(row) for row in rows)
        }

    async def upsert_guild_announcement_surface(
        self,
        surface: AnnouncementSurfaceSettings,
    ) -> AnnouncementSurfaceSettings:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO guild_announcement_surfaces (
                    guild_id,
                    surface_kind,
                    channel_id,
                    image_url,
                    thumbnail_url,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (guild_id, surface_kind) DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    image_url = EXCLUDED.image_url,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                surface.guild_id,
                surface.surface_kind,
                surface.channel_id,
                surface.image_url,
                surface.thumbnail_url,
            )
        return self._map_announcement_surface(row)

    async def delete_guild_announcement_surface(
        self,
        guild_id: int,
        surface_kind: AnnouncementSurfaceKind,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM guild_announcement_surfaces
                WHERE guild_id = $1
                  AND surface_kind = $2
                """,
                guild_id,
                surface_kind,
            )

    async def fetch_guild_experience_settings(
        self,
        guild_id: int,
    ) -> GuildExperienceSettings | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM guild_experience_settings WHERE guild_id = $1",
                guild_id,
            )
        return self._map_guild_experience_settings(row) if row is not None else None

    async def upsert_guild_experience_settings(
        self,
        settings: GuildExperienceSettings,
    ) -> GuildExperienceSettings:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO guild_experience_settings (
                    guild_id,
                    capsules_enabled,
                    quests_enabled,
                    quest_wish_target,
                    quest_reaction_target,
                    quest_checkin_enabled,
                    surprises_enabled,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (guild_id) DO UPDATE SET
                    capsules_enabled = EXCLUDED.capsules_enabled,
                    quests_enabled = EXCLUDED.quests_enabled,
                    quest_wish_target = EXCLUDED.quest_wish_target,
                    quest_reaction_target = EXCLUDED.quest_reaction_target,
                    quest_checkin_enabled = EXCLUDED.quest_checkin_enabled,
                    surprises_enabled = EXCLUDED.surprises_enabled,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                settings.guild_id,
                settings.capsules_enabled,
                settings.quests_enabled,
                settings.quest_wish_target,
                settings.quest_reaction_target,
                settings.quest_checkin_enabled,
                settings.surprises_enabled,
            )
        return self._map_guild_experience_settings(row)

    async def list_guild_surprise_rewards(self, guild_id: int) -> list[GuildSurpriseReward]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM guild_surprise_rewards
                WHERE guild_id = $1
                ORDER BY reward_type ASC
                """,
                guild_id,
            )
        return [self._map_guild_surprise_reward(row) for row in rows]

    async def upsert_guild_surprise_reward(
        self,
        reward: GuildSurpriseReward,
    ) -> GuildSurpriseReward:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO guild_surprise_rewards (
                    guild_id,
                    reward_type,
                    label,
                    weight,
                    enabled,
                    note_text,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (guild_id, reward_type) DO UPDATE SET
                    label = EXCLUDED.label,
                    weight = EXCLUDED.weight,
                    enabled = EXCLUDED.enabled,
                    note_text = EXCLUDED.note_text,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                reward.guild_id,
                reward.reward_type,
                reward.label,
                reward.weight,
                reward.enabled,
                reward.note_text,
            )
        return self._map_guild_surprise_reward(row)

    async def upsert_guild_surprise_rewards(
        self,
        rewards: list[GuildSurpriseReward],
    ) -> list[GuildSurpriseReward]:
        if not rewards:
            return []
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = []
                for reward in rewards:
                    row = await connection.fetchrow(
                        """
                        INSERT INTO guild_surprise_rewards (
                            guild_id,
                            reward_type,
                            label,
                            weight,
                            enabled,
                            note_text,
                            updated_at_utc
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, NOW())
                        ON CONFLICT (guild_id, reward_type) DO UPDATE SET
                            label = EXCLUDED.label,
                            weight = EXCLUDED.weight,
                            enabled = EXCLUDED.enabled,
                            note_text = EXCLUDED.note_text,
                            updated_at_utc = NOW()
                        RETURNING *
                        """,
                        reward.guild_id,
                        reward.reward_type,
                        reward.label,
                        reward.weight,
                        reward.enabled,
                        reward.note_text,
                    )
                    rows.append(row)
        return [self._map_guild_surprise_reward(row) for row in rows]

    async def fetch_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM member_birthdays
                WHERE guild_id = $1 AND user_id = $2
                """,
                guild_id,
                user_id,
            )
        return self._map_member_birthday(row) if row is not None else None

    async def upsert_member_birthday(self, birthday: MemberBirthday) -> MemberBirthday:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO member_birthdays (
                    guild_id,
                    user_id,
                    birth_month,
                    birth_day,
                    birth_year,
                    timezone_override,
                    profile_visibility,
                    next_occurrence_at_utc,
                    next_role_removal_at_utc,
                    active_birthday_role_id,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    birth_month = EXCLUDED.birth_month,
                    birth_day = EXCLUDED.birth_day,
                    birth_year = EXCLUDED.birth_year,
                    timezone_override = EXCLUDED.timezone_override,
                    profile_visibility = EXCLUDED.profile_visibility,
                    next_occurrence_at_utc = EXCLUDED.next_occurrence_at_utc,
                    next_role_removal_at_utc = EXCLUDED.next_role_removal_at_utc,
                    active_birthday_role_id = EXCLUDED.active_birthday_role_id,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                birthday.guild_id,
                birthday.user_id,
                birthday.birth_month,
                birthday.birth_day,
                birthday.birth_year,
                birthday.timezone_override,
                birthday.profile_visibility,
                birthday.next_occurrence_at_utc,
                birthday.next_role_removal_at_utc,
                birthday.active_birthday_role_id,
            )
        return self._map_member_birthday(row)

    async def list_member_birthdays_for_export(self, guild_id: int) -> list[MemberBirthday]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM member_birthdays
                WHERE guild_id = $1
                ORDER BY user_id ASC
                """,
                guild_id,
            )
        return [self._map_member_birthday(row) for row in rows]

    async def list_member_birthday_user_ids(self, guild_id: int, limit: int) -> list[int]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id
                FROM member_birthdays
                WHERE guild_id = $1
                ORDER BY user_id ASC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [row["user_id"] for row in rows]

    async def delete_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    DELETE FROM member_birthdays
                    WHERE guild_id = $1 AND user_id = $2
                    RETURNING *
                    """,
                    guild_id,
                    user_id,
                )
                await connection.execute(
                    """
                    DELETE FROM celebration_events
                    WHERE guild_id = $1
                      AND user_id = $2
                      AND state <> 'completed'
                    """,
                    guild_id,
                    user_id,
                )
                await connection.execute(
                    """
                    DELETE FROM tracked_member_anniversaries
                    WHERE guild_id = $1 AND user_id = $2
                    """,
                    guild_id,
                    user_id,
                )
                await connection.execute(
                    """
                    DELETE FROM birthday_celebrations
                    WHERE guild_id = $1 AND user_id = $2
                    """,
                    guild_id,
                    user_id,
                )
                await connection.execute(
                    """
                    UPDATE birthday_wishes
                    SET state = 'removed',
                        removed_at_utc = NOW(),
                        updated_at_utc = NOW()
                    WHERE guild_id = $1
                      AND target_user_id = $2
                      AND state = 'queued'
                    """,
                    guild_id,
                    user_id,
                )
        return self._map_member_birthday(row) if row is not None else None

    async def fetch_active_birthday_wish(
        self,
        guild_id: int,
        author_user_id: int,
        target_user_id: int,
    ) -> BirthdayWish | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM birthday_wishes
                WHERE guild_id = $1
                  AND author_user_id = $2
                  AND target_user_id = $3
                  AND state = 'queued'
                """,
                guild_id,
                author_user_id,
                target_user_id,
            )
        return self._map_birthday_wish(row) if row is not None else None

    async def upsert_birthday_wish(
        self,
        *,
        guild_id: int,
        author_user_id: int,
        target_user_id: int,
        wish_text: str,
        link_url: str | None,
    ) -> BirthdayWish:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO birthday_wishes (
                    guild_id,
                    author_user_id,
                    target_user_id,
                    wish_text,
                    link_url,
                    state,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, 'queued', NOW())
                ON CONFLICT (guild_id, author_user_id, target_user_id)
                    WHERE state = 'queued'
                DO UPDATE SET
                    wish_text = EXCLUDED.wish_text,
                    link_url = EXCLUDED.link_url,
                    removed_at_utc = NULL,
                    revealed_at_utc = NULL,
                    moderated_by_user_id = NULL,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                guild_id,
                author_user_id,
                target_user_id,
                wish_text,
                link_url,
            )
        return self._map_birthday_wish(row)

    async def list_queued_wishes_by_author(
        self,
        guild_id: int,
        author_user_id: int,
    ) -> list[BirthdayWish]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM birthday_wishes
                WHERE guild_id = $1
                  AND author_user_id = $2
                  AND state = 'queued'
                ORDER BY created_at_utc ASC
                """,
                guild_id,
                author_user_id,
            )
        return [self._map_birthday_wish(row) for row in rows]

    async def remove_birthday_wish(
        self,
        *,
        guild_id: int,
        author_user_id: int,
        target_user_id: int,
        moderator_user_id: int | None = None,
        moderated: bool = False,
    ) -> BirthdayWish | None:
        state = "moderated" if moderated else "removed"
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE birthday_wishes
                SET state = '{state}',
                    removed_at_utc = NOW(),
                    moderated_by_user_id = $4,
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND author_user_id = $2
                  AND target_user_id = $3
                  AND state = 'queued'
                RETURNING *
                """,
                guild_id,
                author_user_id,
                target_user_id,
                moderator_user_id,
            )
        return self._map_birthday_wish(row) if row is not None else None

    async def list_birthday_wishes_for_target(
        self,
        guild_id: int,
        target_user_id: int,
        *,
        state: str,
        occurrence_start_at_utc: datetime | None = None,
    ) -> list[BirthdayWish]:
        occurrence_clause = (
            "AND celebration_occurrence_at_utc = $4" if occurrence_start_at_utc is not None else ""
        )
        params: list[object] = [guild_id, target_user_id, state]
        if occurrence_start_at_utc is not None:
            params.append(occurrence_start_at_utc)
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT *
                FROM birthday_wishes
                WHERE guild_id = $1
                  AND target_user_id = $2
                  AND state = $3
                  {occurrence_clause}
                ORDER BY created_at_utc ASC
                """,
                *params,
            )
        return [self._map_birthday_wish(row) for row in rows]

    async def fetch_latest_birthday_celebration(
        self,
        guild_id: int,
        user_id: int,
    ) -> BirthdayCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM birthday_celebrations
                WHERE guild_id = $1
                  AND user_id = $2
                ORDER BY occurrence_start_at_utc DESC
                LIMIT 1
                """,
                guild_id,
                user_id,
            )
        return self._map_birthday_celebration(row) if row is not None else None

    async def fetch_birthday_celebration(
        self,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
    ) -> BirthdayCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM birthday_celebrations
                WHERE guild_id = $1
                  AND user_id = $2
                  AND occurrence_start_at_utc = $3
                """,
                guild_id,
                user_id,
                occurrence_start_at_utc,
            )
        return self._map_birthday_celebration(row) if row is not None else None

    async def has_tracked_birthday_announcement_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> bool:
        async with self._pool.acquire() as connection:
            exists = await connection.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM birthday_celebrations
                    WHERE guild_id = $1
                      AND announcement_message_id = $2
                )
                """,
                guild_id,
                message_id,
            )
        return bool(exists)

    async def fetch_announcement_channel_for_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> int | None:
        async with self._pool.acquire() as connection:
            channel_id = await connection.fetchval(
                """
                SELECT channel_id
                FROM announcement_batches
                WHERE guild_id = $1
                  AND message_id = $2
                ORDER BY updated_at_utc DESC
                LIMIT 1
                """,
                guild_id,
                message_id,
            )
        return int(channel_id) if isinstance(channel_id, int) else None

    async def list_recent_birthday_celebrations(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int,
    ) -> list[TimelineEntry]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM birthday_celebrations
                WHERE guild_id = $1
                  AND user_id = $2
                ORDER BY occurrence_start_at_utc DESC
                LIMIT $3
                """,
                guild_id,
                user_id,
                limit,
            )
        return [self._map_timeline_entry(row) for row in rows]

    async def fetch_birthday_timeline_stats(
        self,
        guild_id: int,
        user_id: int,
    ) -> tuple[int, int, int, int]:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT
                    COUNT(*) AS celebration_count,
                    COALESCE(SUM(revealed_wish_count), 0) AS wishes_received_count,
                    COUNT(*) FILTER (WHERE quest_completed_at_utc IS NOT NULL) AS quest_badge_count,
                    COUNT(*) FILTER (WHERE surprise_reward_type IS NOT NULL) AS surprise_count
                FROM birthday_celebrations
                WHERE guild_id = $1
                  AND user_id = $2
                """,
                guild_id,
                user_id,
            )
        assert row is not None
        return (
            row["celebration_count"],
            row["wishes_received_count"],
            row["quest_badge_count"],
            row["surprise_count"],
        )

    async def count_featured_birthdays(self, guild_id: int, user_id: int) -> int:
        async with self._pool.acquire() as connection:
            count = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM birthday_celebrations
                WHERE guild_id = $1
                  AND user_id = $2
                  AND featured_birthday = TRUE
                """,
                guild_id,
                user_id,
            )
        assert isinstance(count, int)
        return count

    async def mark_birthday_quest_check_in(
        self,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
        *,
        checked_in_at_utc: datetime,
    ) -> BirthdayCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE birthday_celebrations
                SET quest_checked_in_at_utc = COALESCE(quest_checked_in_at_utc, $4),
                    quest_completed_at_utc = CASE
                        WHEN quest_completed_at_utc IS NOT NULL THEN quest_completed_at_utc
                        WHEN quest_enabled = TRUE
                             AND quest_wish_goal_met = TRUE
                             AND (
                                 quest_reaction_target <= 0
                                 OR quest_reaction_goal_met = TRUE
                             )
                             AND quest_checkin_required = TRUE
                             AND quest_checked_in_at_utc IS NULL THEN $4
                        ELSE quest_completed_at_utc
                    END,
                    featured_birthday = CASE
                        WHEN featured_birthday = TRUE THEN TRUE
                        WHEN quest_enabled = TRUE
                             AND quest_wish_goal_met = TRUE
                             AND (
                                 quest_reaction_target <= 0
                                 OR quest_reaction_goal_met = TRUE
                             )
                             AND quest_checkin_required = TRUE
                             AND quest_checked_in_at_utc IS NULL THEN TRUE
                        ELSE featured_birthday
                    END,
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND user_id = $2
                  AND occurrence_start_at_utc = $3
                RETURNING *
                """,
                guild_id,
                user_id,
                occurrence_start_at_utc,
                checked_in_at_utc,
            )
        return self._map_birthday_celebration(row) if row is not None else None

    async def refresh_birthday_announcement_reactions(
        self,
        guild_id: int,
        message_id: int,
        reaction_count: int,
    ) -> list[BirthdayCelebration]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                UPDATE birthday_celebrations
                SET quest_reaction_count = CASE
                        WHEN quest_reaction_target > 0 THEN $3
                        ELSE quest_reaction_count
                    END,
                    quest_reaction_goal_met = CASE
                        WHEN quest_reaction_target > 0 THEN $3 >= quest_reaction_target
                        ELSE FALSE
                    END,
                    quest_completed_at_utc = CASE
                        WHEN quest_completed_at_utc IS NOT NULL THEN quest_completed_at_utc
                        WHEN quest_enabled = TRUE
                             AND quest_wish_goal_met = TRUE
                             AND (
                                 quest_reaction_target <= 0
                                 OR $3 >= quest_reaction_target
                             )
                             AND (
                                 quest_checkin_required = FALSE
                                 OR quest_checked_in_at_utc IS NOT NULL
                             ) THEN NOW()
                        ELSE NULL
                    END,
                    featured_birthday = CASE
                        WHEN featured_birthday = TRUE THEN TRUE
                        WHEN quest_enabled = TRUE
                             AND quest_wish_goal_met = TRUE
                             AND (
                                 quest_reaction_target <= 0
                                 OR $3 >= quest_reaction_target
                             )
                             AND (
                                 quest_checkin_required = FALSE
                                 OR quest_checked_in_at_utc IS NOT NULL
                             ) THEN TRUE
                        ELSE FALSE
                    END,
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND announcement_message_id = $2
                RETURNING *
                """,
                guild_id,
                message_id,
                reaction_count,
            )
        return [self._map_birthday_celebration(row) for row in rows]

    async def disable_birthday_announcement_reaction_tracking(
        self,
        guild_id: int,
        message_id: int,
    ) -> list[BirthdayCelebration]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                UPDATE birthday_celebrations
                SET quest_reaction_target = 0,
                    quest_reaction_count = 0,
                    quest_reaction_goal_met = FALSE,
                    quest_completed_at_utc = CASE
                        WHEN quest_completed_at_utc IS NOT NULL THEN quest_completed_at_utc
                        WHEN quest_enabled = TRUE
                             AND quest_wish_goal_met = TRUE
                             AND (
                                 quest_checkin_required = FALSE
                                 OR quest_checked_in_at_utc IS NOT NULL
                             ) THEN NOW()
                        ELSE NULL
                    END,
                    featured_birthday = CASE
                        WHEN featured_birthday = TRUE THEN TRUE
                        WHEN quest_enabled = TRUE
                             AND quest_wish_goal_met = TRUE
                             AND (
                                 quest_checkin_required = FALSE
                                 OR quest_checked_in_at_utc IS NOT NULL
                             ) THEN TRUE
                        ELSE FALSE
                    END,
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND announcement_message_id = $2
                  AND quest_reaction_target > 0
                RETURNING *
                """,
                guild_id,
                message_id,
            )
        return [self._map_birthday_celebration(row) for row in rows]

    async def mark_capsule_delivery_result(
        self,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
        *,
        capsule_state: str,
        message_id: int | None = None,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE birthday_celebrations
                SET capsule_state = $4,
                    capsule_message_id = COALESCE($5, capsule_message_id),
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND user_id = $2
                  AND occurrence_start_at_utc = $3
                """,
                guild_id,
                user_id,
                occurrence_start_at_utc,
                capsule_state,
                message_id,
            )

    async def list_pending_nitro_concierge(
        self,
        guild_id: int,
        *,
        limit: int,
    ) -> list[NitroConciergeEntry]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    id,
                    user_id,
                    occurrence_start_at_utc,
                    surprise_reward_label,
                    surprise_note_text,
                    nitro_fulfillment_status
                FROM birthday_celebrations
                WHERE guild_id = $1
                  AND surprise_reward_type = 'nitro_concierge'
                  AND nitro_fulfillment_status = 'pending'
                ORDER BY occurrence_start_at_utc DESC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [
            NitroConciergeEntry(
                celebration_id=row["id"],
                user_id=row["user_id"],
                occurrence_start_at_utc=row["occurrence_start_at_utc"],
                reward_label=row["surprise_reward_label"] or "Nitro concierge",
                note_text=row["surprise_note_text"],
                fulfillment_status=row["nitro_fulfillment_status"],
            )
            for row in rows
        ]

    async def fulfill_nitro_concierge(
        self,
        guild_id: int,
        celebration_id: int,
        *,
        admin_user_id: int,
        fulfillment_status: str,
    ) -> BirthdayCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE birthday_celebrations
                SET nitro_fulfillment_status = $4,
                    nitro_fulfilled_by_user_id = $5,
                    nitro_fulfilled_at_utc = NOW(),
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND id = $2
                  AND surprise_reward_type = 'nitro_concierge'
                  AND nitro_fulfillment_status IS NOT NULL
                RETURNING *
                """,
                guild_id,
                celebration_id,
                fulfillment_status,
                admin_user_id,
            )
        return self._map_birthday_celebration(row) if row is not None else None

    async def count_birthdays_for_month_visibility(
        self,
        guild_id: int,
        month: int,
        *,
        visible_only: bool,
    ) -> int:
        visibility_clause = "AND profile_visibility = 'server_visible'" if visible_only else ""
        async with self._pool.acquire() as connection:
            count = await connection.fetchval(
                f"""
                SELECT COUNT(*)
                FROM member_birthdays
                WHERE guild_id = $1
                  AND birth_month = $2
                  {visibility_clause}
                """,
                guild_id,
                month,
            )
        assert isinstance(count, int)
        return count

    async def count_birthdays_for_day_visibility(
        self,
        guild_id: int,
        month: int,
        day: int,
        *,
        visible_only: bool,
    ) -> int:
        visibility_clause = "AND profile_visibility = 'server_visible'" if visible_only else ""
        async with self._pool.acquire() as connection:
            count = await connection.fetchval(
                f"""
                SELECT COUNT(*)
                FROM member_birthdays
                WHERE guild_id = $1
                  AND birth_month = $2
                  AND birth_day = $3
                  {visibility_clause}
                """,
                guild_id,
                month,
                day,
            )
        assert isinstance(count, int)
        return count

    async def fetch_pending_birthday_occurrences(
        self,
        guild_id: int,
        user_ids: list[int],
        *,
        since_utc: datetime,
    ) -> dict[int, datetime]:
        if not user_ids:
            return {}
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    user_id,
                    MAX(
                        COALESCE(
                            (payload->>'occurrence_start_at_utc')::timestamptz,
                            scheduled_for_utc
                        )
                    ) AS occurrence_start_at_utc
                FROM celebration_events
                WHERE guild_id = $1
                  AND user_id = ANY($2::bigint[])
                  AND state IN ('pending', 'processing')
                  AND event_kind IN (
                      'announcement',
                      'birthday_dm',
                      'role_start',
                      'capsule_reveal'
                  )
                  AND (
                      scheduled_for_utc >= $3
                      OR (
                          payload ? 'occurrence_start_at_utc'
                          AND (payload->>'occurrence_start_at_utc')::timestamptz >= $3
                      )
                  )
                GROUP BY user_id
                """,
                guild_id,
                user_ids,
                since_utc,
            )
        return {
            int(row["user_id"]): row["occurrence_start_at_utc"]
            for row in rows
            if row["occurrence_start_at_utc"] is not None
        }

    async def list_upcoming_birthdays(
        self,
        guild_id: int,
        limit: int,
        *,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        visibility_clause = "AND mb.profile_visibility = 'server_visible'" if visible_only else ""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT
                    mb.user_id,
                    mb.birth_month,
                    mb.birth_day,
                    mb.next_occurrence_at_utc,
                    COALESCE(
                        mb.timezone_override,
                        gs.default_timezone,
                        'UTC'
                    ) AS effective_timezone,
                    mb.profile_visibility
                FROM member_birthdays AS mb
                LEFT JOIN guild_settings AS gs
                    ON gs.guild_id = mb.guild_id
                WHERE mb.guild_id = $1
                  {visibility_clause}
                ORDER BY mb.next_occurrence_at_utc ASC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [self._map_birthday_preview(row) for row in rows]

    async def list_birthdays(
        self,
        guild_id: int,
        limit: int,
        *,
        order_by_upcoming: bool,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        order_clause = (
            "mb.next_occurrence_at_utc ASC"
            if order_by_upcoming
            else "mb.birth_month ASC, mb.birth_day ASC"
        )
        visibility_clause = "AND mb.profile_visibility = 'server_visible'" if visible_only else ""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT
                    mb.user_id,
                    mb.birth_month,
                    mb.birth_day,
                    mb.next_occurrence_at_utc,
                    COALESCE(
                        mb.timezone_override,
                        gs.default_timezone,
                        'UTC'
                    ) AS effective_timezone,
                    mb.profile_visibility
                FROM member_birthdays AS mb
                LEFT JOIN guild_settings AS gs
                    ON gs.guild_id = mb.guild_id
                WHERE mb.guild_id = $1
                  {visibility_clause}
                ORDER BY {order_clause}, mb.user_id ASC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [self._map_birthday_preview(row) for row in rows]

    async def list_birthdays_for_month(
        self,
        guild_id: int,
        month: int,
        limit: int,
        *,
        order_by_upcoming: bool,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        order_clause = "mb.next_occurrence_at_utc ASC" if order_by_upcoming else "mb.birth_day ASC"
        visibility_clause = "AND mb.profile_visibility = 'server_visible'" if visible_only else ""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT
                    mb.user_id,
                    mb.birth_month,
                    mb.birth_day,
                    mb.next_occurrence_at_utc,
                    COALESCE(
                        mb.timezone_override,
                        gs.default_timezone,
                        'UTC'
                    ) AS effective_timezone,
                    mb.profile_visibility
                FROM member_birthdays AS mb
                LEFT JOIN guild_settings AS gs
                    ON gs.guild_id = mb.guild_id
                WHERE mb.guild_id = $1
                  AND mb.birth_month = $2
                  {visibility_clause}
                ORDER BY {order_clause}, mb.user_id ASC
                LIMIT $3
                """,
                guild_id,
                month,
                limit,
            )
        return [self._map_birthday_preview(row) for row in rows]

    async def list_birthdays_for_month_day_pairs(
        self,
        guild_id: int,
        month_day_pairs: tuple[tuple[int, int], ...],
        limit: int,
        *,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        if not month_day_pairs:
            return []
        conditions: list[str] = []
        parameters: list[object] = [guild_id]
        bind_index = 2
        for month, day in month_day_pairs:
            conditions.append(
                f"(mb.birth_month = ${bind_index} AND mb.birth_day = ${bind_index + 1})"
            )
            parameters.extend((month, day))
            bind_index += 2
        parameters.append(limit)
        limit_index = bind_index
        visibility_clause = "AND mb.profile_visibility = 'server_visible'" if visible_only else ""
        where_clause = " OR ".join(conditions)
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT
                    mb.user_id,
                    mb.birth_month,
                    mb.birth_day,
                    mb.next_occurrence_at_utc,
                    COALESCE(
                        mb.timezone_override,
                        gs.default_timezone,
                        'UTC'
                    ) AS effective_timezone,
                    mb.profile_visibility
                FROM member_birthdays AS mb
                LEFT JOIN guild_settings AS gs
                    ON gs.guild_id = mb.guild_id
                WHERE mb.guild_id = $1
                  AND ({where_clause})
                  {visibility_clause}
                ORDER BY mb.next_occurrence_at_utc ASC, mb.user_id ASC
                LIMIT ${limit_index}
                """,
                *parameters,
            )
        return [self._map_birthday_preview(row) for row in rows]

    async def count_birthdays_by_day_for_month(
        self,
        guild_id: int,
        month: int,
        *,
        visible_only: bool,
        limit: int,
    ) -> list[tuple[int, int]]:
        visibility_clause = "AND profile_visibility = 'server_visible'" if visible_only else ""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT birth_day, COUNT(*) AS member_count
                FROM member_birthdays
                WHERE guild_id = $1
                  AND birth_month = $2
                  {visibility_clause}
                GROUP BY birth_day
                ORDER BY member_count DESC, birth_day ASC
                LIMIT $3
                """,
                guild_id,
                month,
                limit,
            )
        return [(row["birth_day"], row["member_count"]) for row in rows]

    async def fetch_tracked_anniversary(
        self,
        guild_id: int,
        user_id: int,
    ) -> TrackedAnniversary | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM tracked_member_anniversaries
                WHERE guild_id = $1 AND user_id = $2
                """,
                guild_id,
                user_id,
            )
        return self._map_tracked_anniversary(row) if row is not None else None

    async def upsert_tracked_anniversary(
        self,
        anniversary: TrackedAnniversary,
    ) -> TrackedAnniversary:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO tracked_member_anniversaries (
                    guild_id,
                    user_id,
                    joined_at_utc,
                    next_occurrence_at_utc,
                    source,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    joined_at_utc = EXCLUDED.joined_at_utc,
                    next_occurrence_at_utc = EXCLUDED.next_occurrence_at_utc,
                    source = EXCLUDED.source,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                anniversary.guild_id,
                anniversary.user_id,
                anniversary.joined_at_utc,
                anniversary.next_occurrence_at_utc,
                anniversary.source,
            )
        return self._map_tracked_anniversary(row)

    async def list_tracked_anniversary_user_ids(self, guild_id: int, limit: int) -> list[int]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id
                FROM tracked_member_anniversaries
                WHERE guild_id = $1
                ORDER BY user_id ASC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [row["user_id"] for row in rows]

    async def fetch_recurring_celebration(
        self,
        guild_id: int,
        celebration_id: int,
    ) -> RecurringCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM recurring_celebrations
                WHERE guild_id = $1 AND id = $2
                """,
                guild_id,
                celebration_id,
            )
        return self._map_recurring_celebration(row) if row is not None else None

    async def fetch_recurring_celebration_by_name(
        self,
        guild_id: int,
        name: str,
    ) -> RecurringCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM recurring_celebrations
                WHERE guild_id = $1
                  AND lower(name) = lower($2)
                """,
                guild_id,
                name,
            )
        return self._map_recurring_celebration(row) if row is not None else None

    async def fetch_server_anniversary(self, guild_id: int) -> RecurringCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM recurring_celebrations
                WHERE guild_id = $1
                  AND celebration_kind = 'server_anniversary'
                """,
                guild_id,
            )
        return self._map_recurring_celebration(row) if row is not None else None

    async def list_recurring_celebrations(
        self,
        guild_id: int,
        *,
        limit: int,
        include_server_anniversary: bool = False,
    ) -> list[RecurringCelebration]:
        kind_clause = "" if include_server_anniversary else "AND celebration_kind = 'custom'"
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT *
                FROM recurring_celebrations
                WHERE guild_id = $1
                  {kind_clause}
                ORDER BY enabled DESC, event_month ASC, event_day ASC, name ASC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [self._map_recurring_celebration(row) for row in rows]

    async def insert_recurring_celebration(
        self,
        *,
        guild_id: int,
        name: str,
        event_month: int,
        event_day: int,
        channel_id: int | None,
        template: str | None,
        enabled: bool,
        celebration_kind: str,
        use_guild_created_date: bool,
        next_occurrence_at_utc: datetime,
    ) -> RecurringCelebration:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO recurring_celebrations (
                    guild_id,
                    name,
                    event_month,
                    event_day,
                    channel_id,
                    template,
                    enabled,
                    celebration_kind,
                    use_guild_created_date,
                    next_occurrence_at_utc,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                RETURNING *
                """,
                guild_id,
                name,
                event_month,
                event_day,
                channel_id,
                template,
                enabled,
                celebration_kind,
                use_guild_created_date,
                next_occurrence_at_utc,
            )
        return self._map_recurring_celebration(row)

    async def update_recurring_celebration(
        self,
        celebration_id: int,
        *,
        guild_id: int,
        name: str,
        event_month: int,
        event_day: int,
        channel_id: int | None,
        template: str | None,
        enabled: bool,
        celebration_kind: str,
        use_guild_created_date: bool,
        next_occurrence_at_utc: datetime,
    ) -> RecurringCelebration | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE recurring_celebrations
                SET name = $3,
                    event_month = $4,
                    event_day = $5,
                    channel_id = $6,
                    template = $7,
                    enabled = $8,
                    celebration_kind = $9,
                    use_guild_created_date = $10,
                    next_occurrence_at_utc = $11,
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND id = $2
                RETURNING *
                """,
                guild_id,
                celebration_id,
                name,
                event_month,
                event_day,
                channel_id,
                template,
                enabled,
                celebration_kind,
                use_guild_created_date,
                next_occurrence_at_utc,
            )
        return self._map_recurring_celebration(row) if row is not None else None

    async def delete_recurring_celebration(
        self,
        guild_id: int,
        celebration_id: int,
    ) -> RecurringCelebration | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    DELETE FROM recurring_celebrations
                    WHERE guild_id = $1
                      AND id = $2
                    RETURNING *
                    """,
                    guild_id,
                    celebration_id,
                )
                await connection.execute(
                    """
                    DELETE FROM celebration_events
                    WHERE guild_id = $1
                      AND event_kind = 'recurring_announcement'
                      AND payload ->> 'recurring_id' = $2
                      AND state <> 'completed'
                    """,
                    guild_id,
                    str(celebration_id),
                )
        return self._map_recurring_celebration(row) if row is not None else None

    async def claim_due_birthdays(self, now_utc: datetime, batch_size: int) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    SELECT
                        mb.*,
                        COALESCE(gs.default_timezone, 'UTC') AS effective_default_timezone,
                        birthday_surface.channel_id AS birthday_surface_channel_id,
                        gs.birthday_role_id,
                        COALESCE(gs.announcements_enabled, FALSE) AS announcements_enabled,
                        COALESCE(gs.role_enabled, FALSE) AS role_enabled,
                        COALESCE(gs.celebration_mode, 'quiet') AS celebration_mode,
                        COALESCE(gs.announcement_theme, 'classic') AS announcement_theme,
                        gs.announcement_template,
                        gs.announcement_title_override,
                        gs.announcement_footer_text,
                        birthday_surface.image_url AS birthday_surface_image_url,
                        birthday_surface.thumbnail_url AS birthday_surface_thumbnail_url,
                        gs.announcement_accent_color,
                        COALESCE(gs.birthday_dm_enabled, FALSE) AS birthday_dm_enabled,
                        gs.birthday_dm_template,
                        gs.eligibility_role_id,
                        COALESCE(gs.ignore_bots, TRUE) AS ignore_bots,
                        COALESCE(gs.minimum_membership_days, 0) AS minimum_membership_days,
                        COALESCE(
                            gs.mention_suppression_threshold,
                            8
                        ) AS mention_suppression_threshold,
                        COALESCE(ges.capsules_enabled, FALSE) AS capsules_enabled,
                        COALESCE(ges.quests_enabled, FALSE) AS quests_enabled,
                        COALESCE(ges.quest_wish_target, 3) AS quest_wish_target,
                        COALESCE(ges.quest_reaction_target, 5) AS quest_reaction_target,
                        COALESCE(ges.quest_checkin_enabled, TRUE) AS quest_checkin_enabled,
                        COALESCE(ges.surprises_enabled, FALSE) AS surprises_enabled
                    FROM member_birthdays AS mb
                    LEFT JOIN guild_settings AS gs
                        ON gs.guild_id = mb.guild_id
                    LEFT JOIN guild_announcement_surfaces AS birthday_surface
                        ON birthday_surface.guild_id = mb.guild_id
                       AND birthday_surface.surface_kind = 'birthday_announcement'
                    LEFT JOIN guild_experience_settings AS ges
                        ON ges.guild_id = mb.guild_id
                    WHERE mb.next_occurrence_at_utc <= $1
                    ORDER BY mb.next_occurrence_at_utc ASC
                    FOR UPDATE OF mb SKIP LOCKED
                    LIMIT $2
                    """,
                    now_utc,
                    batch_size,
                )
                if not rows:
                    return 0

                reward_rows = await connection.fetch(
                    """
                    SELECT *
                    FROM guild_surprise_rewards
                    WHERE guild_id = ANY($1::bigint[])
                      AND enabled = TRUE
                      AND weight > 0
                    ORDER BY guild_id ASC, reward_type ASC
                    """,
                    sorted({row["guild_id"] for row in rows}),
                )
                rewards_by_guild: dict[int, list[GuildSurpriseReward]] = {}
                for reward_row in reward_rows:
                    reward = self._map_guild_surprise_reward(reward_row)
                    rewards_by_guild.setdefault(reward.guild_id, []).append(reward)

                batch_tokens: dict[tuple[int, datetime, int], str] = {}
                for row in rows:
                    channel_id = row["birthday_surface_channel_id"]
                    if channel_id is None or not row["announcements_enabled"]:
                        continue
                    key = (row["guild_id"], row["next_occurrence_at_utc"], channel_id)
                    batch_tokens.setdefault(
                        key,
                        (
                            f"announcement-batch:{row['guild_id']}:"
                            f"{int(row['next_occurrence_at_utc'].timestamp())}:{channel_id}"
                        ),
                    )

                await self._ensure_batch_rows(connection, batch_tokens)

                inserted = 0
                for row in rows:
                    effective_timezone = (
                        row["timezone_override"] or row["effective_default_timezone"]
                    )
                    current_occurrence = row["next_occurrence_at_utc"]
                    next_occurrence = next_occurrence_after_current(
                        birth_month=row["birth_month"],
                        birth_day=row["birth_day"],
                        timezone_name=effective_timezone,
                        current_occurrence_at_utc=current_occurrence,
                    )
                    role_id = row["birthday_role_id"] if row["role_enabled"] else None
                    removal_at = (
                        celebration_end_at_utc(current_occurrence, effective_timezone)
                        if role_id is not None
                        else None
                    )
                    late_delivery = now_utc - current_occurrence > timedelta(minutes=1)
                    await connection.execute(
                        """
                        UPDATE member_birthdays
                        SET next_occurrence_at_utc = $1,
                            next_role_removal_at_utc = $2,
                            active_birthday_role_id = $3,
                            updated_at_utc = NOW()
                        WHERE guild_id = $4 AND user_id = $5
                        """,
                        next_occurrence,
                        removal_at,
                        role_id,
                        row["guild_id"],
                        row["user_id"],
                    )

                    revealed_wish_count = 0
                    capsule_state = "disabled"
                    if row["capsules_enabled"]:
                        revealed_wish_count = await self._reveal_queued_birthday_wishes(
                            connection,
                            guild_id=row["guild_id"],
                            target_user_id=row["user_id"],
                            occurrence_start_at_utc=current_occurrence,
                        )
                        if revealed_wish_count == 0:
                            capsule_state = "no_wishes"
                        elif (
                            row["announcements_enabled"]
                            and row["birthday_surface_channel_id"] is not None
                        ):
                            capsule_state = "pending_public"
                        else:
                            capsule_state = "revealed_private"

                    quest_enabled = bool(row["quests_enabled"])
                    has_public_announcement_route = bool(
                        row["announcements_enabled"]
                        and row["birthday_surface_channel_id"] is not None
                    )
                    quest_wish_target = int(row["quest_wish_target"]) if quest_enabled else 0
                    quest_reaction_target = (
                        int(row["quest_reaction_target"])
                        if quest_enabled and has_public_announcement_route
                        else 0
                    )
                    quest_checkin_required = (
                        bool(row["quest_checkin_enabled"]) if quest_enabled else False
                    )
                    quest_wish_goal_met = (
                        quest_enabled
                        and quest_wish_target > 0
                        and revealed_wish_count >= quest_wish_target
                    )
                    quest_reaction_count = 0
                    quest_reaction_goal_met = (
                        quest_enabled
                        and quest_reaction_target > 0
                        and quest_reaction_count >= quest_reaction_target
                    )
                    quest_completed_at_utc = (
                        now_utc
                        if (
                            quest_enabled
                            and quest_wish_goal_met
                            and (quest_reaction_target <= 0 or quest_reaction_goal_met)
                            and not quest_checkin_required
                        )
                        else None
                    )
                    surprise_reward = (
                        self._select_surprise_reward(
                            rewards_by_guild.get(int(row["guild_id"]), ()),
                            guild_id=int(row["guild_id"]),
                            user_id=int(row["user_id"]),
                            occurrence_start_at_utc=current_occurrence,
                        )
                        if row["surprises_enabled"]
                        else None
                    )
                    featured_birthday = bool(
                        quest_enabled
                        and quest_wish_goal_met
                        and (quest_reaction_target <= 0 or quest_reaction_goal_met)
                        and not quest_checkin_required
                    ) or (
                        surprise_reward is not None and surprise_reward.reward_type == "featured"
                    )
                    await self._upsert_birthday_celebration(
                        connection,
                        guild_id=row["guild_id"],
                        user_id=row["user_id"],
                        occurrence_start_at_utc=current_occurrence,
                        late_delivery=late_delivery,
                        announcement_message_id=None,
                        capsule_state=capsule_state,
                        revealed_wish_count=revealed_wish_count,
                        quest_enabled=quest_enabled,
                        quest_wish_target=quest_wish_target,
                        quest_wish_goal_met=quest_wish_goal_met,
                        quest_reaction_target=quest_reaction_target,
                        quest_reaction_count=quest_reaction_count,
                        quest_reaction_goal_met=quest_reaction_goal_met,
                        quest_checkin_required=quest_checkin_required,
                        quest_completed_at_utc=quest_completed_at_utc,
                        featured_birthday=featured_birthday,
                        surprise_reward=surprise_reward,
                    )

                    if (
                        row["announcements_enabled"]
                        and row["birthday_surface_channel_id"] is not None
                    ):
                        batch_token = batch_tokens[
                            (
                                row["guild_id"],
                                current_occurrence,
                                row["birthday_surface_channel_id"],
                            )
                        ]
                        inserted += await self._insert_event(
                            connection,
                            event_key=(
                                f"announcement:{row['guild_id']}:{row['user_id']}:"
                                f"{int(current_occurrence.timestamp())}"
                            ),
                            guild_id=row["guild_id"],
                            user_id=row["user_id"],
                            event_kind="announcement",
                            scheduled_for_utc=current_occurrence,
                            payload={
                                "channel_id": row["birthday_surface_channel_id"],
                                "batch_token": batch_token,
                                "occurrence_start_at_utc": current_occurrence.isoformat(),
                                "celebration_mode": row["celebration_mode"],
                                "announcement_theme": row["announcement_theme"],
                                "template": row["announcement_template"]
                                or DEFAULT_ANNOUNCEMENT_TEMPLATE,
                                "title_override": row["announcement_title_override"],
                                "footer_text": row["announcement_footer_text"],
                                "image_url": row["birthday_surface_image_url"],
                                "thumbnail_url": row["birthday_surface_thumbnail_url"],
                                "accent_color": row["announcement_accent_color"],
                                "birth_month": row["birth_month"],
                                "birth_day": row["birth_day"],
                                "timezone": effective_timezone,
                                "eligibility_role_id": row["eligibility_role_id"],
                                "ignore_bots": row["ignore_bots"],
                                "minimum_membership_days": row["minimum_membership_days"],
                                "mention_suppression_threshold": row[
                                    "mention_suppression_threshold"
                                ],
                            },
                        )

                    if row["birthday_dm_enabled"]:
                        inserted += await self._insert_event(
                            connection,
                            event_key=(
                                f"birthday-dm:{row['guild_id']}:{row['user_id']}:"
                                f"{int(current_occurrence.timestamp())}"
                            ),
                            guild_id=row["guild_id"],
                            user_id=row["user_id"],
                            event_kind="birthday_dm",
                            scheduled_for_utc=current_occurrence,
                            payload={
                                "occurrence_start_at_utc": current_occurrence.isoformat(),
                                "celebration_mode": row["celebration_mode"],
                                "announcement_theme": row["announcement_theme"],
                                "template": row["birthday_dm_template"] or DEFAULT_DM_TEMPLATE,
                                "birth_month": row["birth_month"],
                                "birth_day": row["birth_day"],
                                "timezone": effective_timezone,
                                "eligibility_role_id": row["eligibility_role_id"],
                                "ignore_bots": row["ignore_bots"],
                                "minimum_membership_days": row["minimum_membership_days"],
                            },
                        )

                    if role_id is not None and removal_at is not None:
                        inserted += await self._insert_event(
                            connection,
                            event_key=(
                                f"role-start:{row['guild_id']}:{row['user_id']}:"
                                f"{int(current_occurrence.timestamp())}"
                            ),
                            guild_id=row["guild_id"],
                            user_id=row["user_id"],
                            event_kind="role_start",
                            scheduled_for_utc=current_occurrence,
                            payload={
                                "role_id": role_id,
                                "occurrence_start_at_utc": current_occurrence.isoformat(),
                                "eligibility_role_id": row["eligibility_role_id"],
                                "ignore_bots": row["ignore_bots"],
                                "minimum_membership_days": row["minimum_membership_days"],
                            },
                        )

                    if (
                        row["capsules_enabled"]
                        and revealed_wish_count > 0
                        and row["announcements_enabled"]
                        and row["birthday_surface_channel_id"] is not None
                    ):
                        inserted += await self._insert_event(
                            connection,
                            event_key=(
                                f"capsule:{row['guild_id']}:{row['user_id']}:"
                                f"{int(current_occurrence.timestamp())}"
                            ),
                            guild_id=row["guild_id"],
                            user_id=row["user_id"],
                            event_kind="capsule_reveal",
                            scheduled_for_utc=current_occurrence + timedelta(seconds=30),
                            payload={
                                "channel_id": row["birthday_surface_channel_id"],
                                "celebration_mode": row["celebration_mode"],
                                "announcement_theme": row["announcement_theme"],
                                "birth_month": row["birth_month"],
                                "birth_day": row["birth_day"],
                                "timezone": effective_timezone,
                                "occurrence_start_at_utc": current_occurrence.isoformat(),
                            },
                        )
                return inserted

    async def claim_due_anniversaries(self, now_utc: datetime, batch_size: int) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    SELECT
                        tma.*,
                        COALESCE(gs.default_timezone, 'UTC') AS default_timezone,
                        COALESCE(gs.celebration_mode, 'quiet') AS celebration_mode,
                        COALESCE(gs.announcement_theme, 'classic') AS announcement_theme,
                        gs.announcement_title_override,
                        gs.announcement_footer_text,
                        gs.announcement_accent_color,
                        gs.anniversary_template,
                        birthday_surface.channel_id AS birthday_surface_channel_id,
                        birthday_surface.image_url AS birthday_surface_image_url,
                        birthday_surface.thumbnail_url AS birthday_surface_thumbnail_url,
                        anniversary_surface.channel_id AS anniversary_surface_channel_id,
                        anniversary_surface.image_url AS anniversary_surface_image_url,
                        anniversary_surface.thumbnail_url AS anniversary_surface_thumbnail_url,
                        COALESCE(gs.anniversary_enabled, FALSE) AS anniversary_enabled,
                        gs.eligibility_role_id,
                        COALESCE(gs.ignore_bots, TRUE) AS ignore_bots,
                        COALESCE(gs.minimum_membership_days, 0) AS minimum_membership_days,
                        COALESCE(
                            gs.mention_suppression_threshold,
                            8
                        ) AS mention_suppression_threshold
                    FROM tracked_member_anniversaries AS tma
                    LEFT JOIN guild_settings AS gs
                        ON gs.guild_id = tma.guild_id
                    LEFT JOIN guild_announcement_surfaces AS birthday_surface
                        ON birthday_surface.guild_id = tma.guild_id
                       AND birthday_surface.surface_kind = 'birthday_announcement'
                    LEFT JOIN guild_announcement_surfaces AS anniversary_surface
                        ON anniversary_surface.guild_id = tma.guild_id
                       AND anniversary_surface.surface_kind = 'anniversary'
                    WHERE tma.next_occurrence_at_utc <= $1
                    ORDER BY tma.next_occurrence_at_utc ASC
                    FOR UPDATE OF tma SKIP LOCKED
                    LIMIT $2
                    """,
                    now_utc,
                    batch_size,
                )
                if not rows:
                    return 0

                batch_tokens: dict[tuple[int, datetime, int], str] = {}
                for row in rows:
                    resolved_surface = resolve_announcement_surface(
                        row["guild_id"],
                        "anniversary",
                        self._announcement_surfaces_from_row(
                            row,
                            "birthday_announcement",
                            "anniversary",
                        ),
                    )
                    channel_id = resolved_surface.channel.effective_value
                    if channel_id is None or not row["anniversary_enabled"]:
                        continue
                    key = (row["guild_id"], row["next_occurrence_at_utc"], channel_id)
                    batch_tokens.setdefault(
                        key,
                        (
                            f"anniversary-batch:{row['guild_id']}:"
                            f"{int(row['next_occurrence_at_utc'].timestamp())}:{channel_id}"
                        ),
                    )

                await self._ensure_batch_rows(connection, batch_tokens)

                inserted = 0
                for row in rows:
                    anniversary_month, anniversary_day = anniversary_month_day(
                        row["joined_at_utc"], row["default_timezone"]
                    )
                    current_occurrence = row["next_occurrence_at_utc"]
                    next_occurrence = next_occurrence_after_current(
                        birth_month=anniversary_month,
                        birth_day=anniversary_day,
                        timezone_name=row["default_timezone"],
                        current_occurrence_at_utc=current_occurrence,
                    )
                    await connection.execute(
                        """
                        UPDATE tracked_member_anniversaries
                        SET next_occurrence_at_utc = $1,
                            updated_at_utc = NOW()
                        WHERE guild_id = $2
                          AND user_id = $3
                        """,
                        next_occurrence,
                        row["guild_id"],
                        row["user_id"],
                    )
                    resolved_surface = resolve_announcement_surface(
                        row["guild_id"],
                        "anniversary",
                        self._announcement_surfaces_from_row(
                            row,
                            "birthday_announcement",
                            "anniversary",
                        ),
                    )
                    channel_id = resolved_surface.channel.effective_value
                    if channel_id is None or not row["anniversary_enabled"]:
                        continue
                    batch_token = batch_tokens[(row["guild_id"], current_occurrence, channel_id)]
                    inserted += await self._insert_event(
                        connection,
                        event_key=(
                            f"anniversary:{row['guild_id']}:{row['user_id']}:"
                            f"{int(current_occurrence.timestamp())}"
                        ),
                        guild_id=row["guild_id"],
                        user_id=row["user_id"],
                        event_kind="anniversary_announcement",
                        scheduled_for_utc=current_occurrence,
                        payload={
                            "channel_id": channel_id,
                            "batch_token": batch_token,
                            "celebration_mode": row["celebration_mode"],
                            "announcement_theme": row["announcement_theme"],
                            "template": row["anniversary_template"] or DEFAULT_ANNIVERSARY_TEMPLATE,
                            "title_override": row["announcement_title_override"],
                            "footer_text": row["announcement_footer_text"],
                            "image_url": resolved_surface.image.effective_value,
                            "thumbnail_url": resolved_surface.thumbnail.effective_value,
                            "accent_color": row["announcement_accent_color"],
                            "joined_at_utc": row["joined_at_utc"].isoformat(),
                            "event_name": "Join anniversary",
                            "event_month": anniversary_month,
                            "event_day": anniversary_day,
                            "eligibility_role_id": row["eligibility_role_id"],
                            "ignore_bots": row["ignore_bots"],
                            "minimum_membership_days": row["minimum_membership_days"],
                            "mention_suppression_threshold": row["mention_suppression_threshold"],
                        },
                    )
                return inserted

    async def claim_due_recurring_celebrations(self, now_utc: datetime, batch_size: int) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    SELECT
                        rc.*,
                        COALESCE(gs.default_timezone, 'UTC') AS default_timezone,
                        COALESCE(gs.celebration_mode, 'quiet') AS celebration_mode,
                        COALESCE(gs.announcement_theme, 'classic') AS announcement_theme,
                        gs.announcement_title_override,
                        gs.announcement_footer_text,
                        gs.announcement_accent_color,
                        birthday_surface.channel_id AS birthday_surface_channel_id,
                        birthday_surface.image_url AS birthday_surface_image_url,
                        birthday_surface.thumbnail_url AS birthday_surface_thumbnail_url,
                        recurring_surface.channel_id AS recurring_surface_channel_id,
                        recurring_surface.image_url AS recurring_surface_image_url,
                        recurring_surface.thumbnail_url AS recurring_surface_thumbnail_url,
                        server_surface.channel_id AS server_surface_channel_id,
                        server_surface.image_url AS server_surface_image_url,
                        server_surface.thumbnail_url AS server_surface_thumbnail_url
                    FROM recurring_celebrations AS rc
                    LEFT JOIN guild_settings AS gs
                        ON gs.guild_id = rc.guild_id
                    LEFT JOIN guild_announcement_surfaces AS birthday_surface
                        ON birthday_surface.guild_id = rc.guild_id
                       AND birthday_surface.surface_kind = 'birthday_announcement'
                    LEFT JOIN guild_announcement_surfaces AS recurring_surface
                        ON recurring_surface.guild_id = rc.guild_id
                       AND recurring_surface.surface_kind = 'recurring_event'
                    LEFT JOIN guild_announcement_surfaces AS server_surface
                        ON server_surface.guild_id = rc.guild_id
                       AND server_surface.surface_kind = 'server_anniversary'
                    WHERE rc.enabled = TRUE
                      AND rc.next_occurrence_at_utc <= $1
                    ORDER BY rc.next_occurrence_at_utc ASC
                    FOR UPDATE OF rc SKIP LOCKED
                    LIMIT $2
                    """,
                    now_utc,
                    batch_size,
                )
                if not rows:
                    return 0
                inserted = 0
                for row in rows:
                    current_occurrence = row["next_occurrence_at_utc"]
                    next_occurrence = next_occurrence_after_current(
                        birth_month=row["event_month"],
                        birth_day=row["event_day"],
                        timezone_name=row["default_timezone"],
                        current_occurrence_at_utc=current_occurrence,
                    )
                    await connection.execute(
                        """
                        UPDATE recurring_celebrations
                        SET next_occurrence_at_utc = $1,
                            updated_at_utc = NOW()
                        WHERE id = $2
                        """,
                        next_occurrence,
                        row["id"],
                    )
                    surface_kind: AnnouncementSurfaceKind = (
                        "server_anniversary"
                        if row["celebration_kind"] == "server_anniversary"
                        else "recurring_event"
                    )
                    resolved_surface = resolve_announcement_surface(
                        row["guild_id"],
                        surface_kind,
                        self._announcement_surfaces_from_row(
                            row,
                            "birthday_announcement",
                            "recurring_event",
                            "server_anniversary",
                        ),
                        event_channel_id=row["channel_id"],
                    )
                    channel_id = resolved_surface.channel.effective_value
                    if channel_id is None:
                        continue
                    inserted += await self._insert_event(
                        connection,
                        event_key=f"recurring:{row['id']}:{int(current_occurrence.timestamp())}",
                        guild_id=row["guild_id"],
                        user_id=None,
                        event_kind="recurring_announcement",
                        scheduled_for_utc=current_occurrence,
                        payload={
                            "channel_id": channel_id,
                            "recurring_id": row["id"],
                            "celebration_mode": row["celebration_mode"],
                            "announcement_theme": row["announcement_theme"],
                            "template": row["template"],
                            "title_override": row["announcement_title_override"],
                            "footer_text": row["announcement_footer_text"],
                            "image_url": resolved_surface.image.effective_value,
                            "thumbnail_url": resolved_surface.thumbnail.effective_value,
                            "accent_color": row["announcement_accent_color"],
                            "event_name": row["name"],
                            "event_month": row["event_month"],
                            "event_day": row["event_day"],
                            "celebration_kind": row["celebration_kind"],
                        },
                    )
                return inserted

    async def skip_stale_birthdays(self, stale_before_utc: datetime, batch_size: int) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    SELECT
                        mb.*,
                        COALESCE(gs.default_timezone, 'UTC') AS effective_default_timezone
                    FROM member_birthdays AS mb
                    LEFT JOIN guild_settings AS gs
                        ON gs.guild_id = mb.guild_id
                    WHERE mb.next_occurrence_at_utc < $1
                    ORDER BY mb.next_occurrence_at_utc ASC
                    FOR UPDATE OF mb SKIP LOCKED
                    LIMIT $2
                    """,
                    stale_before_utc,
                    batch_size,
                )
                if not rows:
                    return 0
                for row in rows:
                    effective_timezone = (
                        row["timezone_override"] or row["effective_default_timezone"]
                    )
                    next_occurrence = next_occurrence_after_current(
                        birth_month=row["birth_month"],
                        birth_day=row["birth_day"],
                        timezone_name=effective_timezone,
                        current_occurrence_at_utc=row["next_occurrence_at_utc"],
                    )
                    await connection.execute(
                        """
                        UPDATE member_birthdays
                        SET next_occurrence_at_utc = $1,
                            updated_at_utc = NOW()
                        WHERE guild_id = $2 AND user_id = $3
                        """,
                        next_occurrence,
                        row["guild_id"],
                        row["user_id"],
                    )
                return len(rows)

    async def claim_due_role_removals(self, now_utc: datetime, batch_size: int) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    SELECT *
                    FROM member_birthdays
                    WHERE next_role_removal_at_utc <= $1
                      AND active_birthday_role_id IS NOT NULL
                    ORDER BY next_role_removal_at_utc ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                    """,
                    now_utc,
                    batch_size,
                )
                if not rows:
                    return 0

                inserted = 0
                for row in rows:
                    removal_at = row["next_role_removal_at_utc"]
                    role_id = row["active_birthday_role_id"]
                    if removal_at is None or role_id is None:
                        continue
                    inserted += await self._insert_event(
                        connection,
                        event_key=(
                            f"role-end:{row['guild_id']}:{row['user_id']}:"
                            f"{int(removal_at.timestamp())}"
                        ),
                        guild_id=row["guild_id"],
                        user_id=row["user_id"],
                        event_kind="role_end",
                        scheduled_for_utc=removal_at,
                        payload={"role_id": role_id},
                    )
                    await connection.execute(
                        """
                        UPDATE member_birthdays
                        SET next_role_removal_at_utc = NULL,
                            active_birthday_role_id = NULL,
                            updated_at_utc = NOW()
                        WHERE guild_id = $1 AND user_id = $2
                        """,
                        row["guild_id"],
                        row["user_id"],
                    )
                return inserted

    async def requeue_stale_processing_events(self, stale_before_utc: datetime) -> int:
        async with self._pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE celebration_events
                SET state = 'pending',
                    processing_started_at_utc = NULL,
                    updated_at_utc = NOW()
                WHERE state = 'processing'
                  AND processing_started_at_utc <= $1
                """,
                stale_before_utc,
            )
        return _parse_affected_rows(result)

    async def claim_pending_events(
        self, now_utc: datetime, batch_size: int
    ) -> list[CelebrationEvent]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                WITH due AS (
                    SELECT id
                    FROM celebration_events
                    WHERE state = 'pending'
                      AND scheduled_for_utc <= $1
                    ORDER BY scheduled_for_utc ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                )
                UPDATE celebration_events AS ce
                SET state = 'processing',
                    processing_started_at_utc = NOW(),
                    updated_at_utc = NOW(),
                    attempt_count = ce.attempt_count + 1
                FROM due
                WHERE ce.id = due.id
                RETURNING ce.*
                """,
                now_utc,
                batch_size,
            )
        return [self._map_celebration_event(row) for row in rows]

    async def mark_events_completed(
        self,
        event_ids: list[int],
        message_id: int | None = None,
        *,
        note_code: str | None = None,
    ) -> None:
        if not event_ids:
            return
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE celebration_events
                    SET state = 'completed',
                        message_id = COALESCE($2, message_id),
                        last_error_code = COALESCE($3, last_error_code),
                        completed_at_utc = NOW(),
                        processing_started_at_utc = NULL,
                        updated_at_utc = NOW()
                    WHERE id = ANY($1::bigint[])
                    """,
                    event_ids,
                    message_id,
                    note_code,
                )
                if message_id is not None:
                    await connection.execute(
                        """
                        UPDATE birthday_celebrations AS bc
                        SET announcement_message_id = COALESCE(bc.announcement_message_id, $2),
                            updated_at_utc = NOW()
                        FROM celebration_events AS ce
                        WHERE ce.id = ANY($1::bigint[])
                          AND ce.event_kind = 'announcement'
                          AND bc.guild_id = ce.guild_id
                          AND bc.user_id = ce.user_id
                          AND bc.occurrence_start_at_utc = ce.scheduled_for_utc
                        """,
                        event_ids,
                        message_id,
                    )

    async def reschedule_events(
        self,
        event_ids: list[int],
        retry_at_utc: datetime,
        error_code: str,
    ) -> None:
        if not event_ids:
            return
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE celebration_events
                SET state = 'pending',
                    scheduled_for_utc = $2,
                    last_error_code = $3,
                    processing_started_at_utc = NULL,
                    updated_at_utc = NOW()
                WHERE id = ANY($1::bigint[])
                """,
                event_ids,
                retry_at_utc,
                error_code,
            )

    async def complete_event_as_skipped(self, event_id: int, error_code: str) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE celebration_events
                    SET state = 'completed',
                        last_error_code = $2,
                        completed_at_utc = NOW(),
                        processing_started_at_utc = NULL,
                        updated_at_utc = NOW()
                    WHERE id = $1
                    """,
                    event_id,
                    error_code,
                )
                await connection.execute(
                    """
                    UPDATE birthday_celebrations AS bc
                    SET quest_reaction_target = 0,
                        quest_reaction_count = 0,
                        quest_reaction_goal_met = FALSE,
                        quest_completed_at_utc = CASE
                            WHEN bc.quest_completed_at_utc IS NOT NULL
                                THEN bc.quest_completed_at_utc
                            WHEN bc.quest_enabled = TRUE
                                 AND bc.quest_wish_goal_met = TRUE
                                 AND (
                                     bc.quest_checkin_required = FALSE
                                     OR bc.quest_checked_in_at_utc IS NOT NULL
                                 ) THEN NOW()
                            ELSE NULL
                        END,
                        featured_birthday = CASE
                            WHEN bc.featured_birthday = TRUE THEN TRUE
                            WHEN bc.quest_enabled = TRUE
                                 AND bc.quest_wish_goal_met = TRUE
                                 AND (
                                     bc.quest_checkin_required = FALSE
                                     OR bc.quest_checked_in_at_utc IS NOT NULL
                                 ) THEN TRUE
                            ELSE FALSE
                        END,
                        updated_at_utc = NOW()
                    FROM celebration_events AS ce
                    WHERE ce.id = $1
                      AND ce.event_kind = 'announcement'
                      AND bc.guild_id = ce.guild_id
                      AND bc.user_id = ce.user_id
                      AND bc.occurrence_start_at_utc = ce.scheduled_for_utc
                      AND bc.quest_reaction_target > 0
                    """,
                    event_id,
                )

    async def complete_events_as_skipped(self, event_ids: list[int], error_code: str) -> None:
        if not event_ids:
            return
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE celebration_events
                    SET state = 'completed',
                        last_error_code = $2,
                        completed_at_utc = NOW(),
                        processing_started_at_utc = NULL,
                        updated_at_utc = NOW()
                    WHERE id = ANY($1::bigint[])
                    """,
                    event_ids,
                    error_code,
                )
                await connection.execute(
                    """
                    UPDATE birthday_celebrations AS bc
                    SET quest_reaction_target = 0,
                        quest_reaction_count = 0,
                        quest_reaction_goal_met = FALSE,
                        quest_completed_at_utc = CASE
                            WHEN bc.quest_completed_at_utc IS NOT NULL
                                THEN bc.quest_completed_at_utc
                            WHEN bc.quest_enabled = TRUE
                                 AND bc.quest_wish_goal_met = TRUE
                                 AND (
                                     bc.quest_checkin_required = FALSE
                                     OR bc.quest_checked_in_at_utc IS NOT NULL
                                 ) THEN NOW()
                            ELSE NULL
                        END,
                        featured_birthday = CASE
                            WHEN bc.featured_birthday = TRUE THEN TRUE
                            WHEN bc.quest_enabled = TRUE
                                 AND bc.quest_wish_goal_met = TRUE
                                 AND (
                                     bc.quest_checkin_required = FALSE
                                     OR bc.quest_checked_in_at_utc IS NOT NULL
                                 ) THEN TRUE
                            ELSE FALSE
                        END,
                        updated_at_utc = NOW()
                    FROM celebration_events AS ce
                    WHERE ce.id = ANY($1::bigint[])
                      AND ce.event_kind = 'announcement'
                      AND bc.guild_id = ce.guild_id
                      AND bc.user_id = ce.user_id
                      AND bc.occurrence_start_at_utc = ce.scheduled_for_utc
                      AND bc.quest_reaction_target > 0
                    """,
                    event_ids,
                )

    async def skip_stale_start_events(self, stale_before_utc: datetime) -> int:
        async with self._pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE celebration_events
                SET state = 'completed',
                    last_error_code = 'recovery_window_expired',
                    completed_at_utc = NOW(),
                    processing_started_at_utc = NULL,
                    updated_at_utc = NOW()
                WHERE event_kind IN (
                    'announcement',
                    'birthday_dm',
                    'anniversary_announcement',
                    'recurring_announcement',
                    'capsule_reveal',
                    'role_start'
                )
                  AND state <> 'completed'
                  AND scheduled_for_utc < $1
                """,
                stale_before_utc,
            )
        return _parse_affected_rows(result)

    async def claim_announcement_events_batch(
        self,
        guild_id: int,
        batch_token: str,
    ) -> list[CelebrationEvent]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE celebration_events
                    SET state = 'processing',
                        processing_started_at_utc = NOW(),
                        updated_at_utc = NOW(),
                        attempt_count = attempt_count + 1
                    WHERE guild_id = $1
                      AND event_kind IN ('announcement', 'anniversary_announcement')
                      AND state = 'pending'
                      AND payload ->> 'batch_token' = $2
                    """,
                    guild_id,
                    batch_token,
                )
                rows = await connection.fetch(
                    """
                    SELECT *
                    FROM celebration_events
                    WHERE guild_id = $1
                      AND event_kind IN ('announcement', 'anniversary_announcement')
                      AND state = 'processing'
                      AND payload ->> 'batch_token' = $2
                    ORDER BY id ASC
                    """,
                    guild_id,
                    batch_token,
                )
        return [self._map_celebration_event(row) for row in rows]

    async def claim_announcement_batch_delivery(
        self,
        batch_token: str,
        *,
        guild_id: int,
        channel_id: int,
        scheduled_for_utc: datetime,
        claimed_at_utc: datetime,
        stale_started_before_utc: datetime,
    ) -> AnnouncementBatchClaim:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT *
                    FROM announcement_batches
                    WHERE batch_token = $1
                    FOR UPDATE
                    """,
                    batch_token,
                )
                if row is None:
                    row = await connection.fetchrow(
                        """
                        INSERT INTO announcement_batches (
                            batch_token,
                            guild_id,
                            channel_id,
                            scheduled_for_utc
                        )
                        VALUES ($1, $2, $3, $4)
                        RETURNING *
                        """,
                        batch_token,
                        guild_id,
                        channel_id,
                        scheduled_for_utc,
                    )
                    assert row is not None

                batch = self._map_announcement_batch(row)
                if batch.state == "sent":
                    return AnnouncementBatchClaim(status="already_sent", batch=batch)
                if (
                    batch.state == "sending"
                    and batch.send_started_at_utc is not None
                    and batch.send_started_at_utc > stale_started_before_utc
                ):
                    return AnnouncementBatchClaim(status="in_flight", batch=batch)

                updated = await connection.fetchrow(
                    """
                    UPDATE announcement_batches
                    SET state = 'sending',
                        send_started_at_utc = $2,
                        updated_at_utc = NOW()
                    WHERE batch_token = $1
                    RETURNING *
                    """,
                    batch_token,
                    claimed_at_utc,
                )
        assert updated is not None
        return AnnouncementBatchClaim(
            status="claimed",
            batch=self._map_announcement_batch(updated),
            needs_history_check=batch.state == "sending",
        )

    async def mark_announcement_batch_sent(
        self,
        batch_token: str,
        *,
        message_id: int | None,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE announcement_batches
                SET state = 'sent',
                    message_id = $2,
                    updated_at_utc = NOW()
                WHERE batch_token = $1
                """,
                batch_token,
                message_id,
            )

    async def reset_announcement_batch_delivery(self, batch_token: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE announcement_batches
                SET state = 'pending',
                    send_started_at_utc = NULL,
                    updated_at_utc = NOW()
                WHERE batch_token = $1
                """,
                batch_token,
            )

    async def next_due_timestamp(self) -> datetime | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT MIN(due_at) AS next_due_at
                FROM (
                    SELECT MIN(next_occurrence_at_utc) AS due_at
                    FROM member_birthdays
                    UNION ALL
                    SELECT MIN(next_occurrence_at_utc) AS due_at
                    FROM tracked_member_anniversaries
                    UNION ALL
                    SELECT MIN(next_occurrence_at_utc) AS due_at
                    FROM recurring_celebrations
                    WHERE enabled = TRUE
                    UNION ALL
                    SELECT MIN(next_role_removal_at_utc) AS due_at
                    FROM member_birthdays
                    WHERE next_role_removal_at_utc IS NOT NULL
                    UNION ALL
                    SELECT MIN(scheduled_for_utc) AS due_at
                    FROM celebration_events
                    WHERE state = 'pending'
                ) AS due_candidates
                """
            )
        return row["next_due_at"] if row is not None else None

    async def fetch_scheduler_backlog(
        self, now_utc: datetime, stale_for: timedelta
    ) -> SchedulerBacklog:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT
                    (SELECT MIN(next_occurrence_at_utc)
                     FROM member_birthdays
                     WHERE next_occurrence_at_utc <= $1) AS oldest_due_birthday_utc,
                    (SELECT MIN(next_occurrence_at_utc)
                     FROM tracked_member_anniversaries
                     WHERE next_occurrence_at_utc <= $1) AS oldest_due_anniversary_utc,
                    (SELECT MIN(next_occurrence_at_utc)
                     FROM recurring_celebrations
                     WHERE enabled = TRUE
                       AND next_occurrence_at_utc <= $1) AS oldest_due_recurring_utc,
                    (SELECT MIN(next_role_removal_at_utc)
                     FROM member_birthdays
                     WHERE next_role_removal_at_utc <= $1) AS oldest_due_role_removal_utc,
                    (SELECT MIN(scheduled_for_utc)
                     FROM celebration_events
                     WHERE state = 'pending'
                       AND scheduled_for_utc <= $1) AS oldest_due_event_utc,
                    (SELECT COUNT(*)
                     FROM celebration_events
                     WHERE state = 'processing'
                       AND processing_started_at_utc <= $2) AS stale_processing_count
                """,
                now_utc,
                now_utc - stale_for,
            )
        return SchedulerBacklog(
            oldest_due_birthday_utc=row["oldest_due_birthday_utc"],
            oldest_due_anniversary_utc=row["oldest_due_anniversary_utc"],
            oldest_due_recurring_utc=row["oldest_due_recurring_utc"],
            oldest_due_role_removal_utc=row["oldest_due_role_removal_utc"],
            oldest_due_event_utc=row["oldest_due_event_utc"],
            stale_processing_count=row["stale_processing_count"],
        )

    async def list_recent_delivery_issues(
        self,
        guild_id: int,
        *,
        since_utc: datetime,
        limit: int,
    ) -> list[RecentDeliveryIssue]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT event_kind, scheduled_for_utc, completed_at_utc, last_error_code, message_id
                FROM celebration_events
                WHERE guild_id = $1
                  AND completed_at_utc >= $2
                  AND last_error_code IS NOT NULL
                  AND last_error_code <> 'late_delivery'
                ORDER BY completed_at_utc DESC
                LIMIT $3
                """,
                guild_id,
                since_utc,
                limit,
            )
        return [
            RecentDeliveryIssue(
                event_kind=row["event_kind"],
                scheduled_for_utc=row["scheduled_for_utc"],
                completed_at_utc=row["completed_at_utc"],
                last_error_code=row["last_error_code"],
                message_id=row["message_id"],
            )
            for row in rows
        ]

    async def fetch_guild_analytics(
        self,
        guild_id: int,
        *,
        since_utc: datetime,
    ) -> GuildAnalytics:
        async with self._pool.acquire() as connection:
            birthday_counts = await connection.fetchrow(
                """
                SELECT
                    COUNT(*) AS birthdays_total,
                    COUNT(*) FILTER (WHERE profile_visibility = 'private') AS birthdays_private,
                    COUNT(*) FILTER (
                        WHERE profile_visibility = 'server_visible'
                    ) AS birthdays_visible
                FROM member_birthdays
                WHERE guild_id = $1
                """,
                guild_id,
            )
            wish_counts = await connection.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE state = 'queued') AS wishes_queued,
                    COUNT(*) FILTER (WHERE state = 'revealed') AS wishes_revealed
                FROM birthday_wishes
                WHERE guild_id = $1
                """,
                guild_id,
            )
            celebration_counts = await connection.fetchrow(
                """
                SELECT
                    COUNT(*) AS celebrations_total,
                    COUNT(*) FILTER (WHERE quest_completed_at_utc IS NOT NULL) AS quest_completions,
                    COUNT(*) FILTER (WHERE surprise_reward_type IS NOT NULL) AS surprises_total,
                    COUNT(*) FILTER (
                        WHERE nitro_fulfillment_status = 'pending'
                    ) AS nitro_pending,
                    COUNT(*) FILTER (
                        WHERE nitro_fulfillment_status = 'delivered'
                    ) AS nitro_delivered,
                    COUNT(*) FILTER (
                        WHERE nitro_fulfillment_status = 'not_delivered'
                    ) AS nitro_not_delivered,
                    COUNT(*) FILTER (
                        WHERE late_delivery = TRUE AND occurrence_start_at_utc >= $2
                    ) AS recent_late_recoveries
                FROM birthday_celebrations
                WHERE guild_id = $1
                """,
                guild_id,
                since_utc,
            )
            tracked_anniversaries = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM tracked_member_anniversaries
                WHERE guild_id = $1
                """,
                guild_id,
            )
            recurring_events = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM recurring_celebrations
                WHERE guild_id = $1
                  AND celebration_kind = 'custom'
                """,
                guild_id,
            )
            most_active_month = await connection.fetchrow(
                """
                SELECT birth_month, COUNT(*) AS member_count
                FROM member_birthdays
                WHERE guild_id = $1
                GROUP BY birth_month
                ORDER BY member_count DESC, birth_month ASC
                LIMIT 1
                """,
                guild_id,
            )
            scheduler_issues = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM celebration_events
                WHERE guild_id = $1
                  AND completed_at_utc >= $2
                  AND last_error_code IS NOT NULL
                  AND last_error_code <> 'late_delivery'
                """,
                guild_id,
                since_utc,
            )
        assert birthday_counts is not None
        assert wish_counts is not None
        assert celebration_counts is not None
        return GuildAnalytics(
            birthdays_total=birthday_counts["birthdays_total"],
            birthdays_private=birthday_counts["birthdays_private"],
            birthdays_visible=birthday_counts["birthdays_visible"],
            wishes_queued=wish_counts["wishes_queued"],
            wishes_revealed=wish_counts["wishes_revealed"],
            celebrations_total=celebration_counts["celebrations_total"],
            quest_completions=celebration_counts["quest_completions"],
            surprises_total=celebration_counts["surprises_total"],
            nitro_pending=celebration_counts["nitro_pending"],
            nitro_delivered=celebration_counts["nitro_delivered"],
            nitro_not_delivered=celebration_counts["nitro_not_delivered"],
            anniversaries_tracked=int(tracked_anniversaries or 0),
            recurring_events_total=int(recurring_events or 0),
            most_active_month=(
                most_active_month["birth_month"] if most_active_month is not None else None
            ),
            most_active_month_count=(
                most_active_month["member_count"] if most_active_month is not None else 0
            ),
            recent_late_recoveries=celebration_counts["recent_late_recoveries"],
            recent_scheduler_issues=int(scheduler_issues or 0),
        )

    async def clear_active_birthday_role(
        self,
        guild_id: int,
        user_id: int,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE member_birthdays
                SET next_role_removal_at_utc = NULL,
                    active_birthday_role_id = NULL,
                    updated_at_utc = NOW()
                WHERE guild_id = $1
                  AND user_id = $2
                """,
                guild_id,
                user_id,
            )

    async def refresh_timezone_bound_schedules(
        self,
        guild_id: int,
        *,
        default_timezone: str,
        now_utc: datetime,
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                birthday_rows = await connection.fetch(
                    """
                    SELECT *
                    FROM member_birthdays
                    WHERE guild_id = $1
                      AND timezone_override IS NULL
                      AND next_occurrence_at_utc > $2
                      AND next_role_removal_at_utc IS NULL
                    FOR UPDATE
                    """,
                    guild_id,
                    now_utc,
                )
                for row in birthday_rows:
                    next_occurrence = next_occurrence_at_utc(
                        birth_month=row["birth_month"],
                        birth_day=row["birth_day"],
                        timezone_name=default_timezone,
                        now_utc=now_utc,
                    )
                    await connection.execute(
                        """
                        UPDATE member_birthdays
                        SET next_occurrence_at_utc = $1,
                            updated_at_utc = NOW()
                        WHERE guild_id = $2
                          AND user_id = $3
                        """,
                        next_occurrence,
                        guild_id,
                        row["user_id"],
                    )

                anniversary_rows = await connection.fetch(
                    """
                    SELECT *
                    FROM tracked_member_anniversaries
                    WHERE guild_id = $1
                      AND next_occurrence_at_utc > $2
                    FOR UPDATE
                    """,
                    guild_id,
                    now_utc,
                )
                for row in anniversary_rows:
                    month, day = anniversary_month_day(row["joined_at_utc"], default_timezone)
                    next_occurrence = next_occurrence_at_utc(
                        birth_month=month,
                        birth_day=day,
                        timezone_name=default_timezone,
                        now_utc=now_utc,
                    )
                    await connection.execute(
                        """
                        UPDATE tracked_member_anniversaries
                        SET next_occurrence_at_utc = $1,
                            updated_at_utc = NOW()
                        WHERE guild_id = $2
                          AND user_id = $3
                        """,
                        next_occurrence,
                        guild_id,
                        row["user_id"],
                    )

                recurring_rows = await connection.fetch(
                    """
                    SELECT *
                    FROM recurring_celebrations
                    WHERE guild_id = $1
                      AND next_occurrence_at_utc > $2
                    FOR UPDATE
                    """,
                    guild_id,
                    now_utc,
                )
                for row in recurring_rows:
                    next_occurrence = next_occurrence_at_utc(
                        birth_month=row["event_month"],
                        birth_day=row["event_day"],
                        timezone_name=default_timezone,
                        now_utc=now_utc,
                    )
                    await connection.execute(
                        """
                        UPDATE recurring_celebrations
                        SET next_occurrence_at_utc = $1,
                            updated_at_utc = NOW()
                        WHERE id = $2
                        """,
                        next_occurrence,
                        row["id"],
                    )

    async def _ensure_batch_rows(
        self,
        connection: asyncpg.Connection,
        batch_tokens: dict[tuple[int, datetime, int], str],
    ) -> None:
        for (guild_id, scheduled_for_utc, channel_id), batch_token in batch_tokens.items():
            await connection.execute(
                """
                INSERT INTO announcement_batches (
                    batch_token,
                    guild_id,
                    channel_id,
                    scheduled_for_utc
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (batch_token) DO NOTHING
                """,
                batch_token,
                guild_id,
                channel_id,
                scheduled_for_utc,
            )

    async def _insert_event(
        self,
        connection: asyncpg.Connection,
        *,
        event_key: str,
        guild_id: int,
        user_id: int | None,
        event_kind: str,
        scheduled_for_utc: datetime,
        payload: dict[str, object],
    ) -> int:
        result = await connection.execute(
            """
            INSERT INTO celebration_events (
                event_key,
                guild_id,
                user_id,
                event_kind,
                scheduled_for_utc,
                payload
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (event_key) DO NOTHING
            """,
            event_key,
            guild_id,
            user_id,
            event_kind,
            scheduled_for_utc,
            json.dumps(payload),
        )
        return 1 if _parse_affected_rows(result) == 1 else 0

    async def _reveal_queued_birthday_wishes(
        self,
        connection: asyncpg.Connection,
        *,
        guild_id: int,
        target_user_id: int,
        occurrence_start_at_utc: datetime,
    ) -> int:
        rows = await connection.fetch(
            """
            UPDATE birthday_wishes
            SET state = 'revealed',
                celebration_occurrence_at_utc = $3,
                revealed_at_utc = NOW(),
                updated_at_utc = NOW()
            WHERE guild_id = $1
              AND target_user_id = $2
              AND state = 'queued'
            RETURNING id
            """,
            guild_id,
            target_user_id,
            occurrence_start_at_utc,
        )
        return len(rows)

    async def _upsert_birthday_celebration(
        self,
        connection: asyncpg.Connection,
        *,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
        late_delivery: bool,
        announcement_message_id: int | None,
        capsule_state: str,
        revealed_wish_count: int,
        quest_enabled: bool,
        quest_wish_target: int,
        quest_wish_goal_met: bool,
        quest_reaction_target: int,
        quest_reaction_count: int,
        quest_reaction_goal_met: bool,
        quest_checkin_required: bool,
        quest_completed_at_utc: datetime | None,
        featured_birthday: bool,
        surprise_reward: GuildSurpriseReward | None,
    ) -> None:
        reward_type = surprise_reward.reward_type if surprise_reward is not None else None
        reward_label = surprise_reward.label if surprise_reward is not None else None
        reward_note = surprise_reward.note_text if surprise_reward is not None else None
        nitro_status = "pending" if reward_type == "nitro_concierge" else None
        await connection.execute(
            """
            INSERT INTO birthday_celebrations (
                guild_id,
                user_id,
                occurrence_start_at_utc,
                late_delivery,
                announcement_message_id,
                capsule_state,
                revealed_wish_count,
                quest_enabled,
                quest_wish_target,
                quest_wish_goal_met,
                quest_reaction_target,
                quest_reaction_count,
                quest_reaction_goal_met,
                quest_checkin_required,
                quest_completed_at_utc,
                featured_birthday,
                surprise_reward_type,
                surprise_reward_label,
                surprise_note_text,
                surprise_selected_at_utc,
                nitro_fulfillment_status,
                updated_at_utc
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                $17, $18, $19,
                CASE WHEN $17 IS NULL THEN NULL ELSE NOW() END,
                $20,
                NOW()
            )
            ON CONFLICT (guild_id, user_id, occurrence_start_at_utc) DO UPDATE SET
                late_delivery = birthday_celebrations.late_delivery OR EXCLUDED.late_delivery,
                announcement_message_id = COALESCE(
                    birthday_celebrations.announcement_message_id,
                    EXCLUDED.announcement_message_id
                ),
                capsule_state = EXCLUDED.capsule_state,
                revealed_wish_count = EXCLUDED.revealed_wish_count,
                quest_enabled = EXCLUDED.quest_enabled,
                quest_wish_target = EXCLUDED.quest_wish_target,
                quest_wish_goal_met = EXCLUDED.quest_wish_goal_met,
                quest_reaction_target = EXCLUDED.quest_reaction_target,
                quest_reaction_count = GREATEST(
                    birthday_celebrations.quest_reaction_count,
                    EXCLUDED.quest_reaction_count
                ),
                quest_reaction_goal_met = (
                    birthday_celebrations.quest_reaction_goal_met
                    OR EXCLUDED.quest_reaction_goal_met
                ),
                quest_checkin_required = EXCLUDED.quest_checkin_required,
                quest_completed_at_utc = COALESCE(
                    birthday_celebrations.quest_completed_at_utc,
                    EXCLUDED.quest_completed_at_utc
                ),
                featured_birthday = birthday_celebrations.featured_birthday
                    OR EXCLUDED.featured_birthday,
                surprise_reward_type = COALESCE(
                    birthday_celebrations.surprise_reward_type,
                    EXCLUDED.surprise_reward_type
                ),
                surprise_reward_label = COALESCE(
                    birthday_celebrations.surprise_reward_label,
                    EXCLUDED.surprise_reward_label
                ),
                surprise_note_text = COALESCE(
                    birthday_celebrations.surprise_note_text,
                    EXCLUDED.surprise_note_text
                ),
                surprise_selected_at_utc = COALESCE(
                    birthday_celebrations.surprise_selected_at_utc,
                    EXCLUDED.surprise_selected_at_utc
                ),
                nitro_fulfillment_status = COALESCE(
                    birthday_celebrations.nitro_fulfillment_status,
                    EXCLUDED.nitro_fulfillment_status
                ),
                updated_at_utc = NOW()
            """,
            guild_id,
            user_id,
            occurrence_start_at_utc,
            late_delivery,
            announcement_message_id,
            capsule_state,
            revealed_wish_count,
            quest_enabled,
            quest_wish_target,
            quest_wish_goal_met,
            quest_reaction_target,
            quest_reaction_count,
            quest_reaction_goal_met,
            quest_checkin_required,
            quest_completed_at_utc,
            featured_birthday,
            reward_type,
            reward_label,
            reward_note,
            nitro_status,
        )

    @staticmethod
    def _select_surprise_reward(
        rewards: list[GuildSurpriseReward] | tuple[GuildSurpriseReward, ...],
        *,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
    ) -> GuildSurpriseReward | None:
        eligible = [reward for reward in rewards if reward.enabled and reward.weight > 0]
        if not eligible:
            return None
        total_weight = sum(reward.weight for reward in eligible)
        if total_weight <= 0:
            return None
        seed = f"{guild_id}:{user_id}:{int(occurrence_start_at_utc.timestamp())}".encode()
        digest = hashlib.sha256(seed).digest()
        ticket = int.from_bytes(digest[:8], "big") % total_weight
        cumulative = 0
        for reward in eligible:
            cumulative += reward.weight
            if ticket < cumulative:
                return reward
        return eligible[-1]

    @staticmethod
    def _announcement_surfaces_from_row(
        row: asyncpg.Record,
        *surface_kinds: AnnouncementSurfaceKind,
    ) -> dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings]:
        prefixes: dict[AnnouncementSurfaceKind, str] = {
            "birthday_announcement": "birthday_surface",
            "anniversary": "anniversary_surface",
            "server_anniversary": "server_surface",
            "recurring_event": "recurring_surface",
        }
        return {
            surface_kind: AnnouncementSurfaceSettings(
                guild_id=row["guild_id"],
                surface_kind=surface_kind,
                channel_id=row[f"{prefixes[surface_kind]}_channel_id"],
                image_url=row[f"{prefixes[surface_kind]}_image_url"],
                thumbnail_url=row[f"{prefixes[surface_kind]}_thumbnail_url"],
            )
            for surface_kind in surface_kinds
        }

    @staticmethod
    def _map_guild_settings(row: asyncpg.Record) -> GuildSettings:
        return GuildSettings(
            guild_id=row["guild_id"],
            default_timezone=row["default_timezone"],
            birthday_role_id=row["birthday_role_id"],
            announcements_enabled=row["announcements_enabled"],
            role_enabled=row["role_enabled"],
            celebration_mode=row["celebration_mode"],
            announcement_theme=row["announcement_theme"],
            announcement_template=row["announcement_template"],
            announcement_title_override=row["announcement_title_override"],
            announcement_footer_text=row["announcement_footer_text"],
            announcement_accent_color=row["announcement_accent_color"],
            birthday_dm_enabled=row["birthday_dm_enabled"],
            birthday_dm_template=row["birthday_dm_template"],
            anniversary_enabled=row["anniversary_enabled"],
            anniversary_template=row["anniversary_template"],
            eligibility_role_id=row["eligibility_role_id"],
            ignore_bots=row["ignore_bots"],
            minimum_membership_days=row["minimum_membership_days"],
            mention_suppression_threshold=row["mention_suppression_threshold"],
            studio_audit_channel_id=row["studio_audit_channel_id"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_announcement_surface(row: asyncpg.Record) -> AnnouncementSurfaceSettings:
        return AnnouncementSurfaceSettings(
            guild_id=row["guild_id"],
            surface_kind=row["surface_kind"],
            channel_id=row["channel_id"],
            image_url=row["image_url"],
            thumbnail_url=row["thumbnail_url"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_guild_experience_settings(row: asyncpg.Record) -> GuildExperienceSettings:
        return GuildExperienceSettings(
            guild_id=row["guild_id"],
            capsules_enabled=row["capsules_enabled"],
            quests_enabled=row["quests_enabled"],
            quest_wish_target=row["quest_wish_target"],
            quest_reaction_target=row["quest_reaction_target"],
            quest_checkin_enabled=row["quest_checkin_enabled"],
            surprises_enabled=row["surprises_enabled"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_guild_surprise_reward(row: asyncpg.Record) -> GuildSurpriseReward:
        return GuildSurpriseReward(
            id=row["id"],
            guild_id=row["guild_id"],
            reward_type=row["reward_type"],
            label=row["label"],
            weight=row["weight"],
            enabled=row["enabled"],
            note_text=row["note_text"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_birthday_preview(row: asyncpg.Record) -> BirthdayPreview:
        return BirthdayPreview(
            user_id=row["user_id"],
            birth_month=row["birth_month"],
            birth_day=row["birth_day"],
            next_occurrence_at_utc=row["next_occurrence_at_utc"],
            effective_timezone=row["effective_timezone"],
            profile_visibility=row["profile_visibility"],
        )

    @staticmethod
    def _map_member_birthday(row: asyncpg.Record) -> MemberBirthday:
        return MemberBirthday(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            birth_month=row["birth_month"],
            birth_day=row["birth_day"],
            birth_year=row["birth_year"],
            timezone_override=row["timezone_override"],
            profile_visibility=row["profile_visibility"],
            next_occurrence_at_utc=row["next_occurrence_at_utc"],
            next_role_removal_at_utc=row["next_role_removal_at_utc"],
            active_birthday_role_id=row["active_birthday_role_id"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_tracked_anniversary(row: asyncpg.Record) -> TrackedAnniversary:
        return TrackedAnniversary(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            joined_at_utc=row["joined_at_utc"],
            next_occurrence_at_utc=row["next_occurrence_at_utc"],
            source=row["source"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_recurring_celebration(row: asyncpg.Record) -> RecurringCelebration:
        return RecurringCelebration(
            id=row["id"],
            guild_id=row["guild_id"],
            name=row["name"],
            event_month=row["event_month"],
            event_day=row["event_day"],
            channel_id=row["channel_id"],
            template=row["template"],
            enabled=row["enabled"],
            celebration_kind=row["celebration_kind"],
            use_guild_created_date=row["use_guild_created_date"],
            next_occurrence_at_utc=row["next_occurrence_at_utc"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_birthday_wish(row: asyncpg.Record) -> BirthdayWish:
        return BirthdayWish(
            id=row["id"],
            guild_id=row["guild_id"],
            author_user_id=row["author_user_id"],
            target_user_id=row["target_user_id"],
            wish_text=row["wish_text"],
            link_url=row["link_url"],
            state=row["state"],
            celebration_occurrence_at_utc=row["celebration_occurrence_at_utc"],
            revealed_at_utc=row["revealed_at_utc"],
            removed_at_utc=row["removed_at_utc"],
            moderated_by_user_id=row["moderated_by_user_id"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_birthday_celebration(row: asyncpg.Record) -> BirthdayCelebration:
        return BirthdayCelebration(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            occurrence_start_at_utc=row["occurrence_start_at_utc"],
            late_delivery=row["late_delivery"],
            announcement_message_id=row["announcement_message_id"],
            capsule_state=row["capsule_state"],
            capsule_message_id=row["capsule_message_id"],
            revealed_wish_count=row["revealed_wish_count"],
            quest_enabled=row["quest_enabled"],
            quest_wish_target=row["quest_wish_target"],
            quest_wish_goal_met=row["quest_wish_goal_met"],
            quest_reaction_target=row["quest_reaction_target"],
            quest_reaction_count=row["quest_reaction_count"],
            quest_reaction_goal_met=row["quest_reaction_goal_met"],
            quest_checkin_required=row["quest_checkin_required"],
            quest_checked_in_at_utc=row["quest_checked_in_at_utc"],
            quest_completed_at_utc=row["quest_completed_at_utc"],
            featured_birthday=row["featured_birthday"],
            surprise_reward_type=row["surprise_reward_type"],
            surprise_reward_label=row["surprise_reward_label"],
            surprise_note_text=row["surprise_note_text"],
            surprise_selected_at_utc=row["surprise_selected_at_utc"],
            nitro_fulfillment_status=row["nitro_fulfillment_status"],
            nitro_fulfilled_by_user_id=row["nitro_fulfilled_by_user_id"],
            nitro_fulfilled_at_utc=row["nitro_fulfilled_at_utc"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _map_timeline_entry(row: asyncpg.Record) -> TimelineEntry:
        return TimelineEntry(
            celebration_id=row["id"],
            occurrence_start_at_utc=row["occurrence_start_at_utc"],
            late_delivery=row["late_delivery"],
            revealed_wish_count=row["revealed_wish_count"],
            quest_completed=row["quest_completed_at_utc"] is not None,
            featured_birthday=row["featured_birthday"],
            surprise_reward_type=row["surprise_reward_type"],
            surprise_reward_label=row["surprise_reward_label"],
            nitro_fulfillment_status=row["nitro_fulfillment_status"],
        )

    @staticmethod
    def _map_celebration_event(row: asyncpg.Record) -> CelebrationEvent:
        raw_payload = row["payload"]
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else dict(raw_payload)
        return CelebrationEvent(
            id=row["id"],
            event_key=row["event_key"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            event_kind=row["event_kind"],
            scheduled_for_utc=row["scheduled_for_utc"],
            state=row["state"],
            payload=payload,
            attempt_count=row["attempt_count"],
            last_error_code=row["last_error_code"],
            message_id=row["message_id"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
            completed_at_utc=row["completed_at_utc"],
            processing_started_at_utc=row["processing_started_at_utc"],
        )

    @staticmethod
    def _map_announcement_batch(row: asyncpg.Record) -> AnnouncementBatch:
        return AnnouncementBatch(
            batch_token=row["batch_token"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            scheduled_for_utc=row["scheduled_for_utc"],
            state=row["state"],
            message_id=row["message_id"],
            send_started_at_utc=row["send_started_at_utc"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )


def _parse_affected_rows(result: str) -> int:
    _, affected = result.split()
    return int(affected)
