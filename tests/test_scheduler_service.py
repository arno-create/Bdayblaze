from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bdayblaze.domain.models import CelebrationEvent, SchedulerMetrics
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    BirthdaySchedulerService,
)


class FakeSchedulerRepository:
    def __init__(self, *, pending_batches: dict[str, list[CelebrationEvent]]) -> None:
        self.pending_batches = pending_batches
        self._claim_pending_calls = 0
        self.completed_calls: list[tuple[list[int], int | None]] = []
        self.skipped_calls: list[tuple[list[int], str]] = []
        self.single_skipped_calls: list[tuple[int, str]] = []
        self.rescheduled_calls: list[tuple[list[int], str]] = []

    async def requeue_stale_processing_events(self, stale_before_utc: datetime) -> int:
        return 0

    async def claim_due_birthdays(self, now_utc: datetime, batch_size: int) -> int:
        return 0

    async def skip_stale_birthdays(self, stale_before_utc: datetime, batch_size: int) -> int:
        return 0

    async def claim_due_role_removals(self, now_utc: datetime, batch_size: int) -> int:
        return 0

    async def claim_pending_events(self, now_utc: datetime, batch_size: int) -> list[CelebrationEvent]:
        self._claim_pending_calls += 1
        if self._claim_pending_calls > 1:
            return []
        first_batch = next(iter(self.pending_batches.values()), [])
        return first_batch

    async def claim_announcement_batch(self, guild_id: int, batch_token: str) -> list[CelebrationEvent]:
        return self.pending_batches[batch_token]

    async def next_due_timestamp(self) -> datetime | None:
        return None

    async def skip_stale_start_events(self, stale_before_utc: datetime) -> int:
        return 0

    async def mark_events_completed(self, event_ids: list[int], message_id: int | None = None) -> None:
        self.completed_calls.append((event_ids, message_id))

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


class FakeGateway:
    def __init__(
        self,
        *,
        existing_message_id: int | None = None,
        role_status: str = "applied",
    ) -> None:
        self.existing_message_id = existing_message_id
        self.role_status = role_status
        self.sent_batches: list[str] = []

    async def find_announcement_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        batch_token: str,
    ) -> int | None:
        return self.existing_message_id

    async def send_birthday_announcement(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_ids: list[int],
        celebration_mode: str,
        batch_token: str,
    ) -> AnnouncementSendResult:
        self.sent_batches.append(batch_token)
        return AnnouncementSendResult(message_id=777)

    async def add_birthday_role(self, *, guild_id: int, user_id: int, role_id: int) -> str:
        return self.role_status

    async def remove_birthday_role(self, *, guild_id: int, user_id: int, role_id: int) -> str:
        return self.role_status


def _announcement_event(event_id: int, batch_token: str) -> CelebrationEvent:
    now = datetime(2026, 3, 24, tzinfo=UTC)
    return CelebrationEvent(
        id=event_id,
        event_key=f"announcement:{event_id}",
        guild_id=1,
        user_id=100 + event_id,
        event_kind="announcement",
        scheduled_for_utc=now,
        state="processing",
        payload={"channel_id": 123, "batch_token": batch_token, "celebration_mode": "quiet"},
        attempt_count=1,
        last_error_code=None,
        message_id=None,
        created_at_utc=now,
        updated_at_utc=now,
        completed_at_utc=None,
        processing_started_at_utc=now,
    )


def _role_event(event_id: int, kind: str) -> CelebrationEvent:
    now = datetime(2026, 3, 24, tzinfo=UTC)
    return CelebrationEvent(
        id=event_id,
        event_key=f"{kind}:{event_id}",
        guild_id=1,
        user_id=42,
        event_kind=kind,  # type: ignore[arg-type]
        scheduled_for_utc=now,
        state="processing",
        payload={"role_id": 999},
        attempt_count=1,
        last_error_code=None,
        message_id=None,
        created_at_utc=now,
        updated_at_utc=now,
        completed_at_utc=None,
        processing_started_at_utc=now,
    )


@pytest.mark.asyncio
async def test_scheduler_marks_existing_announcement_batch_complete_without_resending() -> None:
    batch_token = "announcement-batch:1:123"
    events = [_announcement_event(1, batch_token), _announcement_event(2, batch_token)]
    repository = FakeSchedulerRepository(pending_batches={batch_token: events})
    gateway = FakeGateway(existing_message_id=555)
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
    assert repository.completed_calls == [([1, 2], 555)]
    assert gateway.sent_batches == []


@pytest.mark.asyncio
async def test_scheduler_skips_role_end_when_member_is_missing() -> None:
    event = _role_event(10, "role_end")
    repository = FakeSchedulerRepository(pending_batches={"unused": [event]})
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
