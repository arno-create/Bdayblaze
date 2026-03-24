from __future__ import annotations

import json
from datetime import datetime, timedelta

import asyncpg

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
    BirthdayPreview,
    CelebrationEvent,
    GuildSettings,
    MemberBirthday,
    RecentDeliveryIssue,
    RecurringCelebration,
    SchedulerBacklog,
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
                    announcement_channel_id,
                    default_timezone,
                    birthday_role_id,
                    announcements_enabled,
                    role_enabled,
                    celebration_mode,
                    announcement_theme,
                    announcement_template,
                    announcement_title_override,
                    announcement_footer_text,
                    announcement_image_url,
                    announcement_thumbnail_url,
                    announcement_accent_color,
                    birthday_dm_enabled,
                    birthday_dm_template,
                    anniversary_enabled,
                    anniversary_channel_id,
                    anniversary_template,
                    eligibility_role_id,
                    ignore_bots,
                    minimum_membership_days,
                    mention_suppression_threshold,
                    updated_at_utc
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, NOW()
                )
                ON CONFLICT (guild_id) DO UPDATE SET
                    announcement_channel_id = EXCLUDED.announcement_channel_id,
                    default_timezone = EXCLUDED.default_timezone,
                    birthday_role_id = EXCLUDED.birthday_role_id,
                    announcements_enabled = EXCLUDED.announcements_enabled,
                    role_enabled = EXCLUDED.role_enabled,
                    celebration_mode = EXCLUDED.celebration_mode,
                    announcement_theme = EXCLUDED.announcement_theme,
                    announcement_template = EXCLUDED.announcement_template,
                    announcement_title_override = EXCLUDED.announcement_title_override,
                    announcement_footer_text = EXCLUDED.announcement_footer_text,
                    announcement_image_url = EXCLUDED.announcement_image_url,
                    announcement_thumbnail_url = EXCLUDED.announcement_thumbnail_url,
                    announcement_accent_color = EXCLUDED.announcement_accent_color,
                    birthday_dm_enabled = EXCLUDED.birthday_dm_enabled,
                    birthday_dm_template = EXCLUDED.birthday_dm_template,
                    anniversary_enabled = EXCLUDED.anniversary_enabled,
                    anniversary_channel_id = EXCLUDED.anniversary_channel_id,
                    anniversary_template = EXCLUDED.anniversary_template,
                    eligibility_role_id = EXCLUDED.eligibility_role_id,
                    ignore_bots = EXCLUDED.ignore_bots,
                    minimum_membership_days = EXCLUDED.minimum_membership_days,
                    mention_suppression_threshold = EXCLUDED.mention_suppression_threshold,
                    updated_at_utc = NOW()
                RETURNING *
                """,
                settings.guild_id,
                settings.announcement_channel_id,
                settings.default_timezone,
                settings.birthday_role_id,
                settings.announcements_enabled,
                settings.role_enabled,
                settings.celebration_mode,
                settings.announcement_theme,
                settings.announcement_template,
                settings.announcement_title_override,
                settings.announcement_footer_text,
                settings.announcement_image_url,
                settings.announcement_thumbnail_url,
                settings.announcement_accent_color,
                settings.birthday_dm_enabled,
                settings.birthday_dm_template,
                settings.anniversary_enabled,
                settings.anniversary_channel_id,
                settings.anniversary_template,
                settings.eligibility_role_id,
                settings.ignore_bots,
                settings.minimum_membership_days,
                settings.mention_suppression_threshold,
            )
        return self._map_guild_settings(row)

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
        return self._map_member_birthday(row) if row is not None else None

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
                        gs.announcement_channel_id,
                        gs.birthday_role_id,
                        COALESCE(gs.announcements_enabled, FALSE) AS announcements_enabled,
                        COALESCE(gs.role_enabled, FALSE) AS role_enabled,
                        COALESCE(gs.celebration_mode, 'quiet') AS celebration_mode,
                        COALESCE(gs.announcement_theme, 'classic') AS announcement_theme,
                        gs.announcement_template,
                        gs.announcement_title_override,
                        gs.announcement_footer_text,
                        gs.announcement_image_url,
                        gs.announcement_thumbnail_url,
                        gs.announcement_accent_color,
                        COALESCE(gs.birthday_dm_enabled, FALSE) AS birthday_dm_enabled,
                        gs.birthday_dm_template,
                        gs.eligibility_role_id,
                        COALESCE(gs.ignore_bots, TRUE) AS ignore_bots,
                        COALESCE(gs.minimum_membership_days, 0) AS minimum_membership_days,
                        COALESCE(
                            gs.mention_suppression_threshold,
                            8
                        ) AS mention_suppression_threshold
                    FROM member_birthdays AS mb
                    LEFT JOIN guild_settings AS gs
                        ON gs.guild_id = mb.guild_id
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

                batch_tokens: dict[tuple[int, datetime, int], str] = {}
                for row in rows:
                    channel_id = row["announcement_channel_id"]
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

                    if row["announcements_enabled"] and row["announcement_channel_id"] is not None:
                        batch_token = batch_tokens[
                            (row["guild_id"], current_occurrence, row["announcement_channel_id"])
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
                                "channel_id": row["announcement_channel_id"],
                                "batch_token": batch_token,
                                "celebration_mode": row["celebration_mode"],
                                "announcement_theme": row["announcement_theme"],
                                "template": row["announcement_template"]
                                or DEFAULT_ANNOUNCEMENT_TEMPLATE,
                                "title_override": row["announcement_title_override"],
                                "footer_text": row["announcement_footer_text"],
                                "image_url": row["announcement_image_url"],
                                "thumbnail_url": row["announcement_thumbnail_url"],
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
                                "eligibility_role_id": row["eligibility_role_id"],
                                "ignore_bots": row["ignore_bots"],
                                "minimum_membership_days": row["minimum_membership_days"],
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
                        gs.announcement_image_url,
                        gs.announcement_thumbnail_url,
                        gs.announcement_accent_color,
                        gs.anniversary_template,
                        gs.anniversary_channel_id,
                        gs.announcement_channel_id,
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
                    channel_id = row["anniversary_channel_id"] or row["announcement_channel_id"]
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
                    channel_id = row["anniversary_channel_id"] or row["announcement_channel_id"]
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
                            "image_url": row["announcement_image_url"],
                            "thumbnail_url": row["announcement_thumbnail_url"],
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
                        gs.announcement_image_url,
                        gs.announcement_thumbnail_url,
                        gs.announcement_accent_color,
                        gs.announcement_channel_id
                    FROM recurring_celebrations AS rc
                    LEFT JOIN guild_settings AS gs
                        ON gs.guild_id = rc.guild_id
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
                    channel_id = row["channel_id"] or row["announcement_channel_id"]
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
                            "image_url": row["announcement_image_url"],
                            "thumbnail_url": row["announcement_thumbnail_url"],
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

    async def complete_events_as_skipped(self, event_ids: list[int], error_code: str) -> None:
        if not event_ids:
            return
        async with self._pool.acquire() as connection:
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
                      AND next_role_removal_at_utc IS NULL
                    FOR UPDATE
                    """,
                    guild_id,
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
                    FOR UPDATE
                    """,
                    guild_id,
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
                    FOR UPDATE
                    """,
                    guild_id,
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

    @staticmethod
    def _map_guild_settings(row: asyncpg.Record) -> GuildSettings:
        return GuildSettings(
            guild_id=row["guild_id"],
            announcement_channel_id=row["announcement_channel_id"],
            default_timezone=row["default_timezone"],
            birthday_role_id=row["birthday_role_id"],
            announcements_enabled=row["announcements_enabled"],
            role_enabled=row["role_enabled"],
            celebration_mode=row["celebration_mode"],
            announcement_theme=row["announcement_theme"],
            announcement_template=row["announcement_template"],
            announcement_title_override=row["announcement_title_override"],
            announcement_footer_text=row["announcement_footer_text"],
            announcement_image_url=row["announcement_image_url"],
            announcement_thumbnail_url=row["announcement_thumbnail_url"],
            announcement_accent_color=row["announcement_accent_color"],
            birthday_dm_enabled=row["birthday_dm_enabled"],
            birthday_dm_template=row["birthday_dm_template"],
            anniversary_enabled=row["anniversary_enabled"],
            anniversary_channel_id=row["anniversary_channel_id"],
            anniversary_template=row["anniversary_template"],
            eligibility_role_id=row["eligibility_role_id"],
            ignore_bots=row["ignore_bots"],
            minimum_membership_days=row["minimum_membership_days"],
            mention_suppression_threshold=row["mention_suppression_threshold"],
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
