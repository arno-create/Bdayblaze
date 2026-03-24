from __future__ import annotations

import json
from datetime import datetime, timedelta

import asyncpg

from bdayblaze.domain.announcement_template import DEFAULT_ANNOUNCEMENT_TEMPLATE
from bdayblaze.domain.birthday_logic import celebration_end_at_utc, next_occurrence_after_current
from bdayblaze.domain.models import (
    AnnouncementBatch,
    AnnouncementBatchClaim,
    BirthdayPreview,
    CelebrationEvent,
    GuildSettings,
    MemberBirthday,
    SchedulerBacklog,
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
                    announcement_template,
                    updated_at_utc
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (guild_id) DO UPDATE SET
                    announcement_channel_id = EXCLUDED.announcement_channel_id,
                    default_timezone = EXCLUDED.default_timezone,
                    birthday_role_id = EXCLUDED.birthday_role_id,
                    announcements_enabled = EXCLUDED.announcements_enabled,
                    role_enabled = EXCLUDED.role_enabled,
                    celebration_mode = EXCLUDED.celebration_mode,
                    announcement_template = EXCLUDED.announcement_template,
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
                settings.announcement_template,
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
                    age_visible,
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
                    age_visible = EXCLUDED.age_visible,
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
                birthday.age_visible,
                birthday.next_occurrence_at_utc,
                birthday.next_role_removal_at_utc,
                birthday.active_birthday_role_id,
            )
        return self._map_member_birthday(row)

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
        return self._map_member_birthday(row) if row is not None else None

    async def list_upcoming_birthdays(self, guild_id: int, limit: int) -> list[BirthdayPreview]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    mb.user_id,
                    mb.birth_month,
                    mb.birth_day,
                    mb.next_occurrence_at_utc,
                    COALESCE(mb.timezone_override, gs.default_timezone, 'UTC') AS effective_timezone
                FROM member_birthdays AS mb
                LEFT JOIN guild_settings AS gs
                    ON gs.guild_id = mb.guild_id
                WHERE mb.guild_id = $1
                ORDER BY mb.next_occurrence_at_utc ASC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [
            BirthdayPreview(
                user_id=row["user_id"],
                birth_month=row["birth_month"],
                birth_day=row["birth_day"],
                next_occurrence_at_utc=row["next_occurrence_at_utc"],
                effective_timezone=row["effective_timezone"],
            )
            for row in rows
        ]

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
                        gs.announcement_template
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
                        event_key = (
                            f"announcement:{row['guild_id']}:{row['user_id']}:"
                            f"{int(current_occurrence.timestamp())}"
                        )
                        await connection.execute(
                            """
                            INSERT INTO celebration_events (
                                event_key,
                                guild_id,
                                user_id,
                                event_kind,
                                scheduled_for_utc,
                                payload
                            )
                            VALUES ($1, $2, $3, 'announcement', $4, $5::jsonb)
                            ON CONFLICT (event_key) DO NOTHING
                            """,
                            event_key,
                            row["guild_id"],
                            row["user_id"],
                            current_occurrence,
                            json.dumps(
                                {
                                    "channel_id": row["announcement_channel_id"],
                                    "batch_token": batch_token,
                                    "celebration_mode": row["celebration_mode"],
                                    "template": row["announcement_template"]
                                    or DEFAULT_ANNOUNCEMENT_TEMPLATE,
                                    "birth_month": row["birth_month"],
                                    "birth_day": row["birth_day"],
                                    "timezone": effective_timezone,
                                }
                            ),
                        )
                        inserted += 1

                    if role_id is not None and removal_at is not None:
                        event_key = (
                            f"role-start:{row['guild_id']}:{row['user_id']}:"
                            f"{int(current_occurrence.timestamp())}"
                        )
                        await connection.execute(
                            """
                            INSERT INTO celebration_events (
                                event_key,
                                guild_id,
                                user_id,
                                event_kind,
                                scheduled_for_utc,
                                payload
                            )
                            VALUES ($1, $2, $3, 'role_start', $4, $5::jsonb)
                            ON CONFLICT (event_key) DO NOTHING
                            """,
                            event_key,
                            row["guild_id"],
                            row["user_id"],
                            current_occurrence,
                            json.dumps({"role_id": role_id}),
                        )
                        inserted += 1
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
                    event_key = (
                        f"role-end:{row['guild_id']}:{row['user_id']}:{int(removal_at.timestamp())}"
                    )
                    await connection.execute(
                        """
                        INSERT INTO celebration_events (
                            event_key,
                            guild_id,
                            user_id,
                            event_kind,
                            scheduled_for_utc,
                            payload
                        )
                        VALUES ($1, $2, $3, 'role_end', $4, $5::jsonb)
                        ON CONFLICT (event_key) DO NOTHING
                        """,
                        event_key,
                        row["guild_id"],
                        row["user_id"],
                        removal_at,
                        json.dumps({"role_id": role_id}),
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
                    inserted += 1
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
        self, event_ids: list[int], message_id: int | None = None
    ) -> None:
        if not event_ids:
            return
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE celebration_events
                SET state = 'completed',
                    message_id = COALESCE($2, message_id),
                    completed_at_utc = NOW(),
                    processing_started_at_utc = NULL,
                    updated_at_utc = NOW()
                WHERE id = ANY($1::bigint[])
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
                WHERE event_kind IN ('announcement', 'role_start')
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
                      AND event_kind = 'announcement'
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
                      AND event_kind = 'announcement'
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
            oldest_due_role_removal_utc=row["oldest_due_role_removal_utc"],
            oldest_due_event_utc=row["oldest_due_event_utc"],
            stale_processing_count=row["stale_processing_count"],
        )

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
            announcement_template=row["announcement_template"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
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
            age_visible=row["age_visible"],
            next_occurrence_at_utc=row["next_occurrence_at_utc"],
            next_role_removal_at_utc=row["next_role_removal_at_utc"],
            active_birthday_role_id=row["active_birthday_role_id"],
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
