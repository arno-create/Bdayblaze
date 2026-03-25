from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bdayblaze.domain.models import (
    AnnouncementBatch,
    AnnouncementBatchClaim,
    CelebrationEvent,
    SchedulerMetrics,
)
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    BirthdaySchedulerService,
    DirectSendResult,
    GatewayPermanentError,
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
        self._claim_pending_calls = 0
        self.completed_calls: list[tuple[list[int], int | None, str | None]] = []
        self.skipped_calls: list[tuple[list[int], str]] = []
        self.single_skipped_calls: list[tuple[int, str]] = []
        self.rescheduled_calls: list[tuple[list[int], str]] = []
        self.batch_sent_calls: list[tuple[str, int | None]] = []

    async def requeue_stale_processing_events(self, stale_before_utc: datetime) -> int:
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
    ) -> None:
        self.existing_message_id = existing_message_id
        self.role_status = role_status
        self.dm_result = dm_result or DirectSendResult(status="sent")
        self.recurring_result = recurring_result or DirectSendResult(status="sent", message_id=991)
        self.anniversary_result = anniversary_result or AnnouncementSendResult(message_id=880)
        self.announcement_error = announcement_error
        self.dm_error = dm_error
        self.recurring_error = recurring_error
        self.sent_batches: list[str] = []
        self.sent_anniversary_batches: list[str] = []
        self.history_checks: list[str] = []

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

    async def add_birthday_role(self, **kwargs: object) -> str:
        return self.role_status

    async def remove_birthday_role(self, **kwargs: object) -> str:
        return self.role_status


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
