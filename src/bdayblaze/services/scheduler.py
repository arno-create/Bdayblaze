from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from bdayblaze.domain.models import (
    AnnouncementRecipientSnapshot,
    CelebrationEvent,
    SchedulerMetrics,
)
from bdayblaze.logging import get_logger
from bdayblaze.repositories.postgres import PostgresRepository


class GatewaySkipError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GatewayRetryableError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True, frozen=True)
class AnnouncementSendResult:
    message_id: int


class SchedulerGateway(Protocol):
    async def find_announcement_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        batch_token: str,
    ) -> int | None: ...

    async def send_birthday_announcement(
        self,
        *,
        guild_id: int,
        channel_id: int,
        recipients: list[AnnouncementRecipientSnapshot],
        celebration_mode: str,
        batch_token: str,
        template: str,
    ) -> AnnouncementSendResult: ...

    async def add_birthday_role(self, *, guild_id: int, user_id: int, role_id: int) -> str: ...

    async def remove_birthday_role(self, *, guild_id: int, user_id: int, role_id: int) -> str: ...


class BirthdaySchedulerService:
    def __init__(
        self,
        repository: PostgresRepository,
        gateway: SchedulerGateway,
        metrics: SchedulerMetrics,
        *,
        batch_size: int,
        recovery_grace_hours: int,
        scheduler_max_sleep_seconds: int,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._metrics = metrics
        self._batch_size = batch_size
        self._recovery_grace = timedelta(hours=recovery_grace_hours)
        self._scheduler_max_sleep_seconds = scheduler_max_sleep_seconds
        self._logger = get_logger(component="scheduler")
        self._stale_processing_after = timedelta(minutes=15)
        self._retry_delay = timedelta(minutes=5)

    def attach_gateway(self, gateway: SchedulerGateway) -> None:
        self._gateway = gateway

    async def recover(self, now_utc: datetime | None = None) -> None:
        current = now_utc or datetime.now(UTC)
        skipped_birthdays = await self._skip_stale_birthdays(current)
        skipped_events = await self._repository.skip_stale_start_events(
            current - self._recovery_grace
        )
        reclaimed = await self._repository.requeue_stale_processing_events(
            current - self._stale_processing_after
        )
        if reclaimed or skipped_birthdays or skipped_events:
            self._logger.warning(
                "scheduler_recovery_adjustments",
                requeued_processing_events=reclaimed,
                skipped_birthdays=skipped_birthdays,
                skipped_start_events=skipped_events,
            )
        await self.run_iteration(current)
        self._metrics.recovery_completed = True

    async def run_iteration(self, now_utc: datetime | None = None) -> int:
        current = now_utc or datetime.now(UTC)
        total_claimed = 0
        for _ in range(10):
            await self._skip_stale_birthdays(current)
            await self._repository.skip_stale_start_events(current - self._recovery_grace)
            claimed_birthdays = await self._repository.claim_due_birthdays(
                current, self._batch_size
            )
            claimed_removals = await self._repository.claim_due_role_removals(
                current, self._batch_size
            )
            pending_events = await self._repository.claim_pending_events(current, self._batch_size)
            total_claimed += claimed_birthdays + claimed_removals + len(pending_events)
            if not pending_events and claimed_birthdays == 0 and claimed_removals == 0:
                break
            if pending_events:
                await self._execute_pending_events(current, pending_events)

        self._metrics.last_iteration_at_utc = current
        self._metrics.last_success_at_utc = current
        self._metrics.last_error_code = None
        self._metrics.iterations += 1
        self._metrics.last_claimed_events = total_claimed
        return total_claimed

    async def next_sleep_seconds(self, now_utc: datetime | None = None) -> float:
        current = now_utc or datetime.now(UTC)
        next_due = await self._repository.next_due_timestamp()
        if next_due is None:
            return float(self._scheduler_max_sleep_seconds)
        delta = (next_due - current).total_seconds()
        return max(0.0, min(float(self._scheduler_max_sleep_seconds), delta))

    async def _execute_pending_events(
        self,
        now_utc: datetime,
        pending_events: list[CelebrationEvent],
    ) -> None:
        handled_announcement_batches: set[tuple[int, str]] = set()
        for event in pending_events:
            if event.event_kind == "announcement":
                batch_token = str(event.payload["batch_token"])
                marker = (event.guild_id, batch_token)
                if marker in handled_announcement_batches:
                    continue
                handled_announcement_batches.add(marker)
                await self._handle_announcement_batch(now_utc, event.guild_id, batch_token)
                continue
            await self._handle_role_event(now_utc, event)

    async def _handle_announcement_batch(
        self,
        now_utc: datetime,
        guild_id: int,
        batch_token: str,
    ) -> None:
        batch_events = await self._repository.claim_announcement_events_batch(guild_id, batch_token)
        if not batch_events:
            return
        first_event = batch_events[0]
        channel_id = int(first_event.payload["channel_id"])
        celebration_mode = str(first_event.payload.get("celebration_mode", "quiet"))
        template = str(first_event.payload["template"])
        event_ids = [event.id for event in batch_events]
        batch_claim = await self._repository.claim_announcement_batch_delivery(
            batch_token,
            guild_id=guild_id,
            channel_id=channel_id,
            scheduled_for_utc=first_event.scheduled_for_utc,
            claimed_at_utc=now_utc,
            stale_started_before_utc=now_utc - self._stale_processing_after,
        )
        if batch_claim.status == "already_sent":
            message_id = batch_claim.batch.message_id if batch_claim.batch is not None else None
            await self._repository.mark_events_completed(event_ids, message_id)
            return
        if batch_claim.status == "in_flight":
            return

        if batch_claim.needs_history_check:
            existing_message_id = await self._gateway.find_announcement_message(
                guild_id=guild_id,
                channel_id=channel_id,
                batch_token=batch_token,
            )
            if existing_message_id is not None:
                await self._repository.mark_announcement_batch_sent(
                    batch_token,
                    message_id=existing_message_id,
                )
                await self._repository.mark_events_completed(event_ids, existing_message_id)
                return

        recipients = [
            AnnouncementRecipientSnapshot(
                user_id=event.user_id,
                birth_month=int(event.payload["birth_month"]),
                birth_day=int(event.payload["birth_day"]),
                timezone=str(event.payload["timezone"]),
            )
            for event in batch_events
            if event.user_id is not None
        ]
        if not recipients:
            await self._repository.mark_announcement_batch_sent(batch_token, message_id=None)
            await self._repository.mark_events_completed(event_ids)
            return
        try:
            result = await self._gateway.send_birthday_announcement(
                guild_id=guild_id,
                channel_id=channel_id,
                recipients=recipients,
                celebration_mode=celebration_mode,
                batch_token=batch_token,
                template=template,
            )
        except GatewaySkipError as exc:
            await self._repository.mark_announcement_batch_sent(batch_token, message_id=None)
            await self._repository.complete_events_as_skipped(event_ids, exc.code)
            return
        except GatewayRetryableError as exc:
            await self._repository.reset_announcement_batch_delivery(batch_token)
            await self._repository.reschedule_events(
                event_ids, now_utc + self._retry_delay, exc.code
            )
            return
        await self._repository.mark_announcement_batch_sent(
            batch_token, message_id=result.message_id
        )
        await self._repository.mark_events_completed(event_ids, result.message_id)

    async def _handle_role_event(self, now_utc: datetime, event: CelebrationEvent) -> None:
        if event.user_id is None:
            await self._repository.complete_event_as_skipped(event.id, "missing_user_id")
            return
        role_id = int(event.payload["role_id"])
        try:
            if event.event_kind == "role_start":
                status = await self._gateway.add_birthday_role(
                    guild_id=event.guild_id,
                    user_id=event.user_id,
                    role_id=role_id,
                )
            else:
                status = await self._gateway.remove_birthday_role(
                    guild_id=event.guild_id,
                    user_id=event.user_id,
                    role_id=role_id,
                )
        except GatewayRetryableError as exc:
            await self._repository.reschedule_events(
                [event.id], now_utc + self._retry_delay, exc.code
            )
            return

        if status in {"applied", "already_present", "already_absent"}:
            await self._repository.mark_events_completed([event.id])
            return
        await self._repository.complete_event_as_skipped(event.id, status)

    async def _skip_stale_birthdays(self, now_utc: datetime) -> int:
        skipped_total = 0
        stale_before = now_utc - self._recovery_grace
        for _ in range(10):
            skipped = await self._repository.skip_stale_birthdays(stale_before, self._batch_size)
            skipped_total += skipped
            if skipped == 0:
                break
        return skipped_total


class BirthdaySchedulerRunner:
    def __init__(self, service: BirthdaySchedulerService) -> None:
        self._service = service
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._logger = get_logger(component="scheduler_runner")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop(), name="bdayblaze-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task

    async def _run_loop(self) -> None:
        try:
            await self._service.recover()
        except Exception as exc:
            self._service._metrics.last_error_code = type(exc).__name__
            self._service._metrics.recent_errors.append(type(exc).__name__)
            self._logger.exception("scheduler_recovery_failed", error_code=type(exc).__name__)
        while not self._stop_event.is_set():
            now_utc = datetime.now(UTC)
            try:
                await self._service.run_iteration(now_utc)
            except Exception as exc:
                self._service._metrics.last_error_code = type(exc).__name__
                self._service._metrics.recent_errors.append(type(exc).__name__)
                self._logger.exception("scheduler_iteration_failed", error_code=type(exc).__name__)
            sleep_for = await self._service.next_sleep_seconds(datetime.now(UTC))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
            except TimeoutError:
                continue
