from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bdayblaze.domain.models import (
    AnnouncementBatch,
    AnnouncementBatchClaim,
    BirthdayWish,
    CelebrationEvent,
    RuntimeStatus,
    SchedulerMetrics,
)
from bdayblaze.domain.topgg import VoteBonusStatus
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    BirthdaySchedulerRunner,
    BirthdaySchedulerService,
    DirectSendResult,
    GatewayPermanentError,
    GatewayRetryableError,
)


class FakeSchedulerRepository:
    def __init__(
        self,
        *,
        pending_batches: dict[str, list[CelebrationEvent]],
        batch_claim: AnnouncementBatchClaim,
    ) -> None:
        self.pending_batches = pending_batches
        self.batch_claim = batch_claim
        self.due_vote_reminders: list[object] = []
        self._claim_pending_calls = 0
        self.requeue_processing_calls = 0
        self.completed_calls: list[tuple[list[int], int | None, str | None]] = []
        self.skipped_calls: list[tuple[list[int], str]] = []
        self.single_skipped_calls: list[tuple[int, str]] = []
        self.rescheduled_calls: list[tuple[list[int], str]] = []
        self.batch_sent_calls: list[tuple[str, int | None]] = []
        self.capsule_updates: list[tuple[int, int, datetime, str, int | None]] = []
        self.revealed_wishes: list[BirthdayWish] = []
        self.sent_vote_reminders: list[tuple[int, datetime, datetime]] = []
        self.skipped_vote_reminders: list[tuple[int, datetime, str]] = []
        self.retried_vote_reminders: list[tuple[int, datetime, datetime, str]] = []

    async def requeue_stale_processing_events(self, stale_before_utc: datetime) -> int:
        self.requeue_processing_calls += 1
        return 0

    async def claim_due_birthdays(self, now_utc: datetime, batch_size: int) -> int:
        return 0

    async def claim_due_anniversaries(self, now_utc: datetime, batch_size: int) -> int:
        return 0

    async def claim_due_recurring_celebrations(self, now_utc: datetime, batch_size: int) -> int:
        return 0

    async def skip_stale_birthdays(self, stale_before_utc: datetime, batch_size: int) -> int:
        return 0

    async def claim_due_role_removals(self, now_utc: datetime, batch_size: int) -> int:
        return 0

    async def claim_pending_events(
        self,
        now_utc: datetime,
        batch_size: int,
    ) -> list[CelebrationEvent]:
        self._claim_pending_calls += 1
        if self._claim_pending_calls > 1:
            return []
        return next(iter(self.pending_batches.values()), [])

    async def claim_announcement_events_batch(
        self,
        guild_id: int,
        batch_token: str,
    ) -> list[CelebrationEvent]:
        return self.pending_batches[batch_token]

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
        return self.batch_claim

    async def next_due_timestamp(self) -> datetime | None:
        return None

    async def claim_due_topgg_vote_reminders(
        self,
        now_utc: datetime,
        batch_size: int,
    ) -> list[object]:
        due = [
            reminder
            for reminder in self.due_vote_reminders
            if getattr(reminder, "scheduled_reminder_at", None) is not None
            and getattr(reminder, "scheduled_reminder_at") <= now_utc
        ]
        claimed = due[:batch_size]
        for reminder in claimed:
            self.due_vote_reminders.remove(reminder)
        return claimed

    async def mark_topgg_vote_reminder_sent(
        self,
        discord_user_id: int,
        *,
        vote_expires_at: datetime,
        reminded_at: datetime,
    ) -> None:
        self.sent_vote_reminders.append((discord_user_id, vote_expires_at, reminded_at))

    async def reschedule_topgg_vote_reminder_retry(
        self,
        discord_user_id: int,
        *,
        vote_expires_at: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> None:
        self.retried_vote_reminders.append((discord_user_id, vote_expires_at, retry_at, error_code))

    async def mark_topgg_vote_reminder_skipped(
        self,
        discord_user_id: int,
        *,
        vote_expires_at: datetime,
        error_code: str,
    ) -> None:
        self.skipped_vote_reminders.append((discord_user_id, vote_expires_at, error_code))

    async def skip_stale_start_events(self, stale_before_utc: datetime) -> int:
        return 0

    async def mark_events_completed(
        self,
        event_ids: list[int],
        message_id: int | None = None,
        *,
        note_code: str | None = None,
    ) -> None:
        self.completed_calls.append((event_ids, message_id, note_code))

    async def complete_events_as_skipped(self, event_ids: list[int], error_code: str) -> None:
        self.skipped_calls.append((event_ids, error_code))

    async def complete_event_as_skipped(self, event_id: int, error_code: str) -> None:
        self.single_skipped_calls.append((event_id, error_code))

    async def reschedule_events(
        self,
        event_ids: list[int],
        retry_at_utc: datetime,
        error_code: str,
    ) -> None:
        self.rescheduled_calls.append((event_ids, error_code))

    async def mark_announcement_batch_sent(
        self,
        batch_token: str,
        *,
        message_id: int | None,
    ) -> None:
        self.batch_sent_calls.append((batch_token, message_id))

    async def list_birthday_wishes_for_target(
        self,
        guild_id: int,
        target_user_id: int,
        *,
        state: str,
        occurrence_start_at_utc: datetime | None = None,
    ) -> list[BirthdayWish]:
        return list(self.revealed_wishes)

    async def mark_capsule_delivery_result(
        self,
        guild_id: int,
        user_id: int,
        occurrence_start_at_utc: datetime,
        *,
        capsule_state: str,
        message_id: int | None = None,
    ) -> None:
        self.capsule_updates.append(
            (guild_id, user_id, occurrence_start_at_utc, capsule_state, message_id)
        )


class FakeGateway:
    def __init__(
        self,
        *,
        existing_message_id: int | None = None,
        role_status: str = "applied",
        dm_result: DirectSendResult | None = None,
        recurring_result: DirectSendResult | None = None,
        anniversary_result: AnnouncementSendResult | None = None,
        announcement_error: Exception | None = None,
        dm_error: Exception | None = None,
        recurring_error: Exception | None = None,
        capsule_result: DirectSendResult | None = None,
    ) -> None:
        self.existing_message_id = existing_message_id
        self.role_status = role_status
        self.dm_result = dm_result or DirectSendResult(status="sent")
        self.recurring_result = recurring_result or DirectSendResult(status="sent", message_id=991)
        self.anniversary_result = anniversary_result or AnnouncementSendResult(message_id=880)
        self.announcement_error = announcement_error
        self.dm_error = dm_error
        self.recurring_error = recurring_error
        self.capsule_result = capsule_result or DirectSendResult(status="sent", message_id=551)
        self.vote_reminder_result = DirectSendResult(status="sent")
        self.vote_reminder_error: Exception | None = None
        self.sent_batches: list[str] = []
        self.sent_anniversary_batches: list[str] = []
        self.history_checks: list[str] = []
        self.sent_capsules: list[int] = []
        self.sent_vote_reminders: list[int] = []

    async def find_announcement_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        batch_token: str,
        announcement_theme: str,
        scheduled_for_utc: datetime,
        send_started_at_utc: datetime | None,
    ) -> int | None:
        self.history_checks.append(batch_token)
        return self.existing_message_id

    async def send_birthday_announcement(self, **kwargs: object) -> AnnouncementSendResult:
        if self.announcement_error is not None:
            raise self.announcement_error
        self.sent_batches.append(str(kwargs["batch_token"]))
        return AnnouncementSendResult(message_id=777)

    async def send_anniversary_announcement(self, **kwargs: object) -> AnnouncementSendResult:
        if self.announcement_error is not None:
            raise self.announcement_error
        self.sent_anniversary_batches.append(str(kwargs["batch_token"]))
        return self.anniversary_result

    async def send_birthday_dm(self, **kwargs: object) -> DirectSendResult:
        if self.dm_error is not None:
            raise self.dm_error
        return self.dm_result

    async def send_recurring_announcement(self, **kwargs: object) -> DirectSendResult:
        if self.recurring_error is not None:
            raise self.recurring_error
        return self.recurring_result

    async def send_capsule_reveal(self, **kwargs: object) -> DirectSendResult:
        self.sent_capsules.append(int(kwargs["user_id"]))
        return self.capsule_result

    async def send_topgg_vote_reminder(self, **kwargs: object) -> DirectSendResult:
        if self.vote_reminder_error is not None:
            raise self.vote_reminder_error
        self.sent_vote_reminders.append(int(kwargs["user_id"]))
        return self.vote_reminder_result

    async def add_birthday_role(self, **kwargs: object) -> str:
        return self.role_status

    async def remove_birthday_role(self, **kwargs: object) -> str:
        return self.role_status


class FakeRunnerService:
    def __init__(
        self,
        *,
        iteration_error: Exception | None = None,
        sleep_error: Exception | None = None,
        sleep_seconds: float = 0.01,
    ) -> None:
        self._metrics = SchedulerMetrics()
        self.iteration_error = iteration_error
        self.sleep_error = sleep_error
        self.sleep_seconds = sleep_seconds
        self.run_calls = 0
        self.sleep_calls = 0

    async def recover(self, now_utc: datetime | None = None) -> None:
        self._metrics.recovery_completed = True

    async def run_iteration(self, now_utc: datetime | None = None) -> int:
        self.run_calls += 1
        if self.iteration_error is not None:
            raise self.iteration_error
        current = now_utc or datetime.now(UTC)
        self._metrics.last_iteration_at_utc = current
        self._metrics.last_success_at_utc = current
        self._metrics.last_activity_at_utc = current
        self._metrics.last_error_code = None
        return 0

    async def next_sleep_seconds(self, now_utc: datetime | None = None) -> float:
        self.sleep_calls += 1
        if self.sleep_error is not None:
            raise self.sleep_error
        return self.sleep_seconds


class FakeVoteService:
    def __init__(self, *, status: VoteBonusStatus, vote_url: str = "https://top.gg/bot/1485920716573380660/vote") -> None:
        self.status = status
        self.vote_url = vote_url
        self.calls: list[int] = []

    async def get_vote_bonus_status(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> VoteBonusStatus:
        self.calls.append(discord_user_id)
        return self.status


def _announcement_batch(
    *,
    batch_token: str,
    state: str,
    message_id: int | None,
) -> AnnouncementBatch:
    now = datetime(2026, 3, 24, tzinfo=UTC)
    return AnnouncementBatch(
        batch_token=batch_token,
        guild_id=1,
        channel_id=123,
        scheduled_for_utc=now,
        state=state,  # type: ignore[arg-type]
        message_id=message_id,
        send_started_at_utc=now,
        created_at_utc=now,
        updated_at_utc=now,
    )


def _announcement_event(
    event_id: int,
    batch_token: str,
    *,
    kind: str = "announcement",
) -> CelebrationEvent:
    now = datetime(2026, 3, 24, tzinfo=UTC)
    payload: dict[str, object] = {
        "channel_id": 123,
        "batch_token": batch_token,
        "celebration_mode": "quiet",
        "announcement_theme": "classic",
        "template": "Happy birthday {birthday.mentions}",
        "birth_month": 3,
        "birth_day": 24,
        "timezone": "Asia/Yerevan",
    }
    if kind == "anniversary_announcement":
        payload = {
            "channel_id": 123,
            "batch_token": batch_token,
            "celebration_mode": "quiet",
            "announcement_theme": "classic",
            "template": "Happy anniversary {members.names}",
            "joined_at_utc": now.isoformat(),
            "event_name": "Join anniversary",
            "event_month": 3,
            "event_day": 24,
        }
    return CelebrationEvent(
        id=event_id,
        event_key=f"{kind}:{event_id}",
        guild_id=1,
        user_id=100 + event_id,
        event_kind=kind,  # type: ignore[arg-type]
        scheduled_for_utc=now,
        state="processing",
        payload=payload,
        attempt_count=1,
        last_error_code=None,
        message_id=None,
        created_at_utc=now,
        updated_at_utc=now,
        completed_at_utc=None,
        processing_started_at_utc=now,
    )


def _single_event(event_id: int, kind: str, payload: dict[str, object]) -> CelebrationEvent:
    now = datetime(2026, 3, 24, tzinfo=UTC)
    return CelebrationEvent(
        id=event_id,
        event_key=f"{kind}:{event_id}",
        guild_id=1,
        user_id=42,
        event_kind=kind,  # type: ignore[arg-type]
        scheduled_for_utc=now,
        state="processing",
        payload=payload,
        attempt_count=1,
        last_error_code=None,
        message_id=None,
        created_at_utc=now,
        updated_at_utc=now,
        completed_at_utc=None,
        processing_started_at_utc=now,
    )


def _vote_status(
    *,
    lane_state: str = "active_exact",
    timing_source: str | None = "exact",
) -> VoteBonusStatus:
    return VoteBonusStatus(
        lane_state=lane_state,  # type: ignore[arg-type]
        enabled=True,
        active=lane_state.startswith("active"),
        configuration_message=None,
        voted_at_utc=datetime(2026, 3, 24, 10, tzinfo=UTC),
        expires_at_utc=datetime(2026, 3, 24, 12, tzinfo=UTC),
        timing_source=timing_source,  # type: ignore[arg-type]
        weight=1,
        refresh_available=False,
        refresh_cooldown_seconds=60,
        refresh_retry_after_seconds=None,
        wish_character_limit=500,
        timeline_entry_limit=12,
        reminders_enabled=True,
        reminder_lane_state="armed_exact",
        next_reminder_at_utc=datetime(2026, 3, 24, 11, 30, tzinfo=UTC),
        last_reminder_error_code=None,
        reminder_timing_source=timing_source,  # type: ignore[arg-type]
    )


def _due_vote_reminder(
    *,
    discord_user_id: int = 42,
    vote_expires_at: datetime | None = None,
    scheduled_reminder_at: datetime | None = None,
    timing_source: str = "exact",
    attempt_count: int = 0,
) -> object:
    return SimpleNamespace(
        discord_user_id=discord_user_id,
        enabled=True,
        scheduled_vote_expires_at=vote_expires_at or datetime(2026, 3, 24, 12, tzinfo=UTC),
        scheduled_reminder_at=scheduled_reminder_at
        or datetime(2026, 3, 24, 11, 30, tzinfo=UTC),
        processing_started_at=None,
        last_reminded_vote_expires_at=None,
        last_reminded_at=None,
        attempt_count=attempt_count,
        last_error_code=None,
        timing_source=timing_source,
    )


@pytest.mark.asyncio
async def test_scheduler_marks_already_sent_batch_complete_without_resending() -> None:
    batch_token = "announcement-batch:1:123"
    events = [_announcement_event(1, batch_token), _announcement_event(2, batch_token)]
    repository = FakeSchedulerRepository(
        pending_batches={batch_token: events},
        batch_claim=AnnouncementBatchClaim(
            status="already_sent",
            batch=_announcement_batch(batch_token=batch_token, state="sent", message_id=555),
        ),
    )
    gateway = FakeGateway()
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 2
    assert repository.completed_calls == [([1, 2], 555, None)]
    assert gateway.sent_batches == []
    assert gateway.history_checks == []


@pytest.mark.asyncio
async def test_scheduler_uses_history_scan_only_for_stale_sending_batch_recovery() -> None:
    batch_token = "announcement-batch:1:123"
    events = [_announcement_event(1, batch_token), _announcement_event(2, batch_token)]
    repository = FakeSchedulerRepository(
        pending_batches={batch_token: events},
        batch_claim=AnnouncementBatchClaim(
            status="claimed",
            batch=_announcement_batch(batch_token=batch_token, state="sending", message_id=None),
            needs_history_check=True,
        ),
    )
    gateway = FakeGateway(existing_message_id=777)
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 2
    assert gateway.history_checks == [batch_token]
    assert gateway.sent_batches == []
    assert repository.batch_sent_calls == [(batch_token, 777)]
    assert repository.completed_calls == [([1, 2], 777, None)]


@pytest.mark.asyncio
async def test_scheduler_handles_anniversary_batches() -> None:
    batch_token = "anniversary-batch:1:123"
    events = [
        _announcement_event(1, batch_token, kind="anniversary_announcement"),
        _announcement_event(2, batch_token, kind="anniversary_announcement"),
    ]
    repository = FakeSchedulerRepository(
        pending_batches={batch_token: events},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway()
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 2
    assert gateway.sent_anniversary_batches == [batch_token]
    assert repository.completed_calls == [([1, 2], 880, None)]


@pytest.mark.asyncio
async def test_scheduler_marks_announcement_batch_skipped_for_permanent_payload_error() -> None:
    batch_token = "announcement-batch:1:123"
    events = [_announcement_event(1, batch_token), _announcement_event(2, batch_token)]
    repository = FakeSchedulerRepository(
        pending_batches={batch_token: events},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway(announcement_error=GatewayPermanentError("invalid_media_url"))
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 2
    assert repository.batch_sent_calls == [(batch_token, None)]
    assert repository.skipped_calls == [([1, 2], "invalid_media_url")]
    assert repository.rescheduled_calls == []


@pytest.mark.asyncio
async def test_scheduler_completes_birthday_dm_as_skipped_when_member_cannot_be_dmed() -> None:
    event = _single_event(
        11,
        "birthday_dm",
        {
            "template": "Happy birthday {user.display_name}",
            "birth_month": 3,
            "birth_day": 24,
            "timezone": "UTC",
        },
    )
    repository = FakeSchedulerRepository(
        pending_batches={"unused": [event]},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway(dm_result=DirectSendResult(status="dm_closed"))
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 1
    assert repository.single_skipped_calls == [(11, "dm_closed")]


@pytest.mark.asyncio
async def test_scheduler_marks_birthday_dm_skipped_for_permanent_payload_error() -> None:
    event = _single_event(
        13,
        "birthday_dm",
        {
            "template": "Happy birthday {user.display_name}",
            "birth_month": 3,
            "birth_day": 24,
            "timezone": "UTC",
        },
    )
    repository = FakeSchedulerRepository(
        pending_batches={"unused": [event]},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway(dm_error=GatewayPermanentError("invalid_birthday_dm_payload"))
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 1
    assert repository.single_skipped_calls == [(13, "invalid_birthday_dm_payload")]
    assert repository.rescheduled_calls == []


@pytest.mark.asyncio
async def test_scheduler_marks_recurring_announcement_complete() -> None:
    event = _single_event(
        12,
        "recurring_announcement",
        {
            "channel_id": 123,
            "event_name": "Server birthday",
            "event_month": 3,
            "event_day": 24,
        },
    )
    repository = FakeSchedulerRepository(
        pending_batches={"unused": [event]},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway(recurring_result=DirectSendResult(status="sent", message_id=991))
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 1
    assert repository.completed_calls == [([12], 991, None)]


@pytest.mark.asyncio
async def test_scheduler_marks_recurring_announcement_skipped_for_permanent_payload_error() -> None:
    event = _single_event(
        14,
        "recurring_announcement",
        {
            "channel_id": 123,
            "event_name": "Server birthday",
            "event_month": 3,
            "event_day": 24,
        },
    )
    repository = FakeSchedulerRepository(
        pending_batches={"unused": [event]},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway(recurring_error=GatewayPermanentError("invalid_announcement_payload"))
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 1
    assert repository.single_skipped_calls == [(14, "invalid_announcement_payload")]
    assert repository.rescheduled_calls == []


@pytest.mark.asyncio
async def test_scheduler_posts_capsule_reveal_and_marks_public_delivery() -> None:
    occurrence = datetime(2026, 3, 24, tzinfo=UTC)
    event = _single_event(
        15,
        "capsule_reveal",
        {
            "channel_id": 123,
            "celebration_mode": "quiet",
            "announcement_theme": "classic",
            "birth_month": 3,
            "birth_day": 24,
            "timezone": "UTC",
            "occurrence_start_at_utc": occurrence.isoformat(),
        },
    )
    repository = FakeSchedulerRepository(
        pending_batches={"unused": [event]},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    repository.revealed_wishes = [
        BirthdayWish(
            id=1,
            guild_id=1,
            author_user_id=7,
            target_user_id=42,
            wish_text="Happy birthday",
            link_url=None,
            state="revealed",
            celebration_occurrence_at_utc=occurrence,
            revealed_at_utc=occurrence,
            removed_at_utc=None,
            moderated_by_user_id=None,
            created_at_utc=occurrence,
            updated_at_utc=occurrence,
        )
    ]
    gateway = FakeGateway()
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(occurrence)

    assert claimed == 1
    assert gateway.sent_capsules == [42]
    assert repository.completed_calls == [([15], 551, None)]
    assert repository.capsule_updates == [(1, 42, occurrence, "posted_public", 551)]


@pytest.mark.asyncio
async def test_scheduler_skips_role_end_when_member_is_missing() -> None:
    event = _single_event(10, "role_end", {"role_id": 999})
    repository = FakeSchedulerRepository(
        pending_batches={"unused": [event]},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    gateway = FakeGateway(role_status="member_missing")
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert claimed == 1
    assert repository.single_skipped_calls == [(10, "member_missing")]


@pytest.mark.asyncio
async def test_scheduler_run_iteration_requeues_stale_processing_during_steady_state() -> None:
    repository = FakeSchedulerRepository(
        pending_batches={},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        FakeGateway(),  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
    )

    await service.run_iteration(datetime(2026, 3, 24, tzinfo=UTC))

    assert repository.requeue_processing_calls == 1


@pytest.mark.asyncio
async def test_scheduler_runner_survives_sleep_calculation_failure() -> None:
    service = FakeRunnerService(sleep_error=ValueError("sleep failed"))
    runner = BirthdaySchedulerRunner(
        service,  # type: ignore[arg-type]
        RuntimeStatus(process_started_at_utc=datetime.now(UTC)),
    )

    runner.start()
    await asyncio.sleep(0.02)

    assert runner._task is not None
    assert runner._task.done() is False
    assert service._metrics.last_error_code == "ValueError"
    assert service._metrics.last_activity_at_utc is not None

    await runner.stop()


def test_scheduler_runner_caps_recent_errors() -> None:
    service = FakeRunnerService()
    runner = BirthdaySchedulerRunner(
        service,  # type: ignore[arg-type]
        RuntimeStatus(process_started_at_utc=datetime.now(UTC)),
    )

    for _ in range(25):
        runner._record_failure(RuntimeError("boom"))

    assert len(service._metrics.recent_errors) == 20
    assert service._metrics.recent_errors == ["RuntimeError"] * 20


@pytest.mark.asyncio
async def test_scheduler_sends_due_vote_reminder_once_and_marks_it_sent() -> None:
    repository = FakeSchedulerRepository(
        pending_batches={},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    repository.due_vote_reminders = [_due_vote_reminder()]
    gateway = FakeGateway()
    vote_service = FakeVoteService(status=_vote_status())
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
        vote_service=vote_service,  # type: ignore[arg-type]
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, 11, 31, tzinfo=UTC))

    assert claimed == 1
    assert gateway.sent_vote_reminders == [42]
    assert repository.sent_vote_reminders == [
        (
            42,
            datetime(2026, 3, 24, 12, tzinfo=UTC),
            datetime(2026, 3, 24, 11, 31, tzinfo=UTC),
        )
    ]
    assert repository.retried_vote_reminders == []
    assert repository.skipped_vote_reminders == []


@pytest.mark.asyncio
async def test_scheduler_retries_vote_reminder_after_retryable_dm_error() -> None:
    repository = FakeSchedulerRepository(
        pending_batches={},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    repository.due_vote_reminders = [_due_vote_reminder()]
    gateway = FakeGateway()
    gateway.vote_reminder_error = GatewayRetryableError("discord_http_error")
    vote_service = FakeVoteService(status=_vote_status())
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
        vote_service=vote_service,  # type: ignore[arg-type]
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, 11, 31, tzinfo=UTC))

    assert claimed == 1
    assert repository.sent_vote_reminders == []
    assert repository.retried_vote_reminders == [
        (
            42,
            datetime(2026, 3, 24, 12, tzinfo=UTC),
            datetime(2026, 3, 24, 11, 36, tzinfo=UTC),
            "discord_http_error",
        )
    ]


@pytest.mark.asyncio
async def test_scheduler_skips_vote_reminder_for_dm_forbidden_without_public_fallback() -> None:
    repository = FakeSchedulerRepository(
        pending_batches={},
        batch_claim=AnnouncementBatchClaim(status="claimed", batch=None),
    )
    repository.due_vote_reminders = [_due_vote_reminder()]
    gateway = FakeGateway()
    gateway.vote_reminder_result = DirectSendResult(status="dm_forbidden")
    vote_service = FakeVoteService(status=_vote_status())
    service = BirthdaySchedulerService(
        repository,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        SchedulerMetrics(),
        batch_size=25,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
        vote_service=vote_service,  # type: ignore[arg-type]
    )

    claimed = await service.run_iteration(datetime(2026, 3, 24, 11, 31, tzinfo=UTC))

    assert claimed == 1
    assert repository.skipped_vote_reminders == [
        (42, datetime(2026, 3, 24, 12, tzinfo=UTC), "dm_forbidden")
    ]
    assert gateway.sent_batches == []
    assert gateway.sent_anniversary_batches == []
    assert gateway.sent_capsules == []
