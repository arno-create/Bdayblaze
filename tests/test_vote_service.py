from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bdayblaze.config import Settings
from bdayblaze.services.vote_service import VoteService


class FakeReminderRecord:
    def __init__(
        self,
        *,
        discord_user_id: int,
        enabled: bool,
        scheduled_vote_expires_at: datetime | None,
        scheduled_reminder_at: datetime | None,
        processing_started_at: datetime | None = None,
        last_reminded_vote_expires_at: datetime | None = None,
        last_reminded_at: datetime | None = None,
        attempt_count: int = 0,
        last_error_code: str | None = None,
        timing_source: str | None = None,
    ) -> None:
        self.discord_user_id = discord_user_id
        self.enabled = enabled
        self.scheduled_vote_expires_at = scheduled_vote_expires_at
        self.scheduled_reminder_at = scheduled_reminder_at
        self.processing_started_at = processing_started_at
        self.last_reminded_vote_expires_at = last_reminded_vote_expires_at
        self.last_reminded_at = last_reminded_at
        self.attempt_count = attempt_count
        self.last_error_code = last_error_code
        self.timing_source = timing_source
        self.created_at = datetime(2026, 4, 24, 10, tzinfo=UTC)
        self.updated_at = datetime(2026, 4, 24, 10, tzinfo=UTC)


class FakeVoteRepository:
    def __init__(self) -> None:
        self.receipts: dict[str, object] = {}
        self.reminders: dict[int, FakeReminderRecord] = {}
        self.storage_ready = True
        self.storage_message = "Top.gg storage is ready."

    async def insert_topgg_vote_receipt(self, receipt: object) -> bool:
        event_id = getattr(receipt, "event_id")
        if event_id in self.receipts:
            return False
        self.receipts[event_id] = receipt
        return True

    async def fetch_latest_topgg_vote_receipt(self, discord_user_id: int) -> object | None:
        candidates = [
            receipt
            for receipt in self.receipts.values()
            if getattr(receipt, "discord_user_id") == discord_user_id
            and getattr(receipt, "status") == "processed"
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda receipt: (
                getattr(receipt, "vote_expires_at") or datetime.min.replace(tzinfo=UTC),
                getattr(receipt, "processed_at") or getattr(receipt, "received_at"),
            ),
            reverse=True,
        )
        return candidates[0]

    async def list_recent_topgg_vote_receipts(
        self,
        discord_user_id: int,
        *,
        limit: int,
    ) -> list[object]:
        rows = [
            receipt
            for receipt in self.receipts.values()
            if getattr(receipt, "discord_user_id") == discord_user_id
        ]
        rows.sort(
            key=lambda receipt: getattr(receipt, "received_at"),
            reverse=True,
        )
        return rows[:limit]

    async def probe_topgg_storage(self) -> tuple[bool, str]:
        return self.storage_ready, self.storage_message

    async def fetch_topgg_vote_reminder(self, discord_user_id: int) -> object | None:
        return self.reminders.get(discord_user_id)

    async def upsert_topgg_vote_reminder_preference(
        self,
        discord_user_id: int,
        *,
        enabled: bool,
        now_utc: datetime,
    ) -> object:
        reminder = self.reminders.get(discord_user_id)
        if reminder is None:
            reminder = FakeReminderRecord(
                discord_user_id=discord_user_id,
                enabled=enabled,
                scheduled_vote_expires_at=None,
                scheduled_reminder_at=None,
            )
        reminder.enabled = enabled
        reminder.updated_at = now_utc
        self.reminders[discord_user_id] = reminder
        return reminder

    async def schedule_topgg_vote_reminder(
        self,
        discord_user_id: int,
        *,
        vote_expires_at: datetime,
        reminder_at: datetime,
        timing_source: str,
        now_utc: datetime,
    ) -> object:
        reminder = self.reminders.get(discord_user_id)
        if reminder is None:
            reminder = FakeReminderRecord(
                discord_user_id=discord_user_id,
                enabled=True,
                scheduled_vote_expires_at=vote_expires_at,
                scheduled_reminder_at=reminder_at,
                timing_source=timing_source,
            )
        reminder.enabled = True
        reminder.scheduled_vote_expires_at = vote_expires_at
        reminder.scheduled_reminder_at = reminder_at
        reminder.processing_started_at = None
        reminder.attempt_count = 0
        reminder.last_error_code = None
        reminder.timing_source = timing_source
        reminder.updated_at = now_utc
        self.reminders[discord_user_id] = reminder
        return reminder

    async def clear_topgg_vote_reminder_schedule(
        self,
        discord_user_id: int,
        *,
        keep_enabled: bool,
        now_utc: datetime,
    ) -> object:
        reminder = self.reminders.get(discord_user_id)
        if reminder is None:
            reminder = FakeReminderRecord(
                discord_user_id=discord_user_id,
                enabled=keep_enabled,
                scheduled_vote_expires_at=None,
                scheduled_reminder_at=None,
            )
        reminder.enabled = keep_enabled
        reminder.scheduled_vote_expires_at = None
        reminder.scheduled_reminder_at = None
        reminder.processing_started_at = None
        reminder.attempt_count = 0
        reminder.timing_source = None
        reminder.updated_at = now_utc
        self.reminders[discord_user_id] = reminder
        return reminder


def _settings(
    *,
    enabled: bool,
    secret: str = "",
    token: str = "",
    cooldown_seconds: int = 60,
) -> Settings:
    return Settings(
        discord_token="discord-token",
        database_url="postgresql://postgres:postgres@localhost:5432/bdayblaze",
        log_level="INFO",
        auto_run_migrations=False,
        recovery_grace_hours=36,
        scheduler_max_sleep_seconds=300,
        scheduler_batch_size=25,
        guild_sync_ids=(),
        bind_host="0.0.0.0",
        bind_port=8080,
        topgg_enabled=enabled,
        topgg_bot_id=1485920716573380660,
        topgg_webhook_secret=secret,
        topgg_token=token,
        topgg_v2_replay_window_seconds=300,
        topgg_refresh_cooldown_seconds=cooldown_seconds,
    )


def _v2_vote_payload(
    *,
    event_id: str = "808499215864008704",
    created_at: str = "2026-04-24T12:00:00+00:00",
    expires_at: str = "2026-04-25T00:00:00+00:00",
    user_platform_id: str = "222",
    weight: int = 1,
) -> bytes:
    return json.dumps(
        {
            "type": "vote.create",
            "data": {
                "id": event_id,
                "weight": weight,
                "created_at": created_at,
                "expires_at": expires_at,
                "project": {
                    "id": "803190510032756736",
                    "type": "bot",
                    "platform": "discord",
                    "platform_id": "1485920716573380660",
                },
                "user": {
                    "id": "topgg-user",
                    "platform_id": user_platform_id,
                    "name": "jamie",
                    "avatar_url": "https://example.com/avatar.png",
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _v2_signature(secret: str, *, timestamp: int, payload: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


@pytest.mark.asyncio
async def test_diagnostics_snapshot_reports_disabled_vote_lane_cleanly() -> None:
    service = VoteService(
        FakeVoteRepository(),
        settings=_settings(enabled=False),
    )

    snapshot = service.diagnostics_snapshot()

    assert snapshot["enabled"] is False
    assert snapshot["configuration_state"] == "disabled"
    assert snapshot["webhook_mode"] is None
    assert snapshot["refresh_available"] is False
    assert snapshot["timing_source"] is None


@pytest.mark.asyncio
async def test_initialize_storage_state_reports_probe_result_truthfully() -> None:
    repository = FakeVoteRepository()
    repository.storage_ready = False
    repository.storage_message = "Top.gg storage probe failed."
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )

    await service.initialize_storage_state()
    snapshot = service.diagnostics_snapshot()

    assert snapshot["storage_ready"] is False
    assert snapshot["storage_message"] == "Top.gg storage probe failed."
    assert snapshot["reminder_delivery_mode"] == "dm"


@pytest.mark.asyncio
async def test_handle_webhook_rejects_invalid_v2_signature() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload()

    result = await service.handle_webhook(
        headers={"x-topgg-signature": "t=1777000000,v1=bad"},
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 5, tzinfo=UTC),
    )

    assert result.http_status == 400
    assert result.outcome == "invalid_signature"
    assert repository.receipts == {}


@pytest.mark.asyncio
async def test_handle_webhook_rejects_stale_v2_replay() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload()
    now_utc = datetime(2026, 4, 24, 12, 5, tzinfo=UTC)
    stale_timestamp = int((now_utc - timedelta(minutes=6)).timestamp())

    result = await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=stale_timestamp,
                payload=payload,
            )
        },
        raw_body=payload,
        now_utc=now_utc,
    )

    assert result.http_status == 400
    assert result.outcome == "stale"
    assert repository.receipts == {}


@pytest.mark.asyncio
async def test_handle_webhook_rejects_malformed_json_body() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    raw_body = b"{not-json"
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())

    result = await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=timestamp,
                payload=raw_body,
            )
        },
        raw_body=raw_body,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )

    assert result.http_status == 400
    assert result.outcome == "invalid_payload"
    assert repository.receipts == {}


@pytest.mark.asyncio
async def test_handle_webhook_rejects_invalid_vote_window() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload(
        created_at="2026-04-24T12:00:00+00:00",
        expires_at="2026-04-24T11:59:59+00:00",
    )
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())

    result = await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=timestamp,
                payload=payload,
            )
        },
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )

    assert result.http_status == 400
    assert result.outcome == "invalid_payload"
    assert repository.receipts == {}


@pytest.mark.asyncio
async def test_handle_webhook_rejects_wrong_project_platform_id() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = json.dumps(
        {
            "type": "vote.create",
            "data": {
                "id": "808499215864008704",
                "weight": 1,
                "created_at": "2026-04-24T12:00:00+00:00",
                "expires_at": "2026-04-25T00:00:00+00:00",
                "project": {
                    "id": "803190510032756736",
                    "type": "bot",
                    "platform": "discord",
                    "platform_id": "999999999999",
                },
                "user": {
                    "id": "topgg-user",
                    "platform_id": "222",
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())

    result = await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=timestamp,
                payload=payload,
            )
        },
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )

    assert result.http_status == 400
    assert result.outcome == "invalid_payload"


@pytest.mark.asyncio
async def test_handle_webhook_rejects_invalid_user_platform_id() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload(user_platform_id="0")
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())

    result = await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=timestamp,
                payload=payload,
            )
        },
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )

    assert result.http_status == 400
    assert result.outcome == "invalid_payload"


@pytest.mark.asyncio
async def test_handle_webhook_is_idempotent_for_duplicate_v2_delivery() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload()
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())
    headers = {
        "x-topgg-signature": _v2_signature(
            "whs_test_secret",
            timestamp=timestamp,
            payload=payload,
        )
    }

    first = await service.handle_webhook(
        headers=headers,
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )
    duplicate = await service.handle_webhook(
        headers=headers,
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 2, tzinfo=UTC),
    )
    status = await service.get_vote_bonus_status(
        222,
        now_utc=datetime(2026, 4, 24, 12, 3, tzinfo=UTC),
    )

    assert first.outcome == "processed"
    assert duplicate.outcome == "duplicate"
    assert len(repository.receipts) == 1
    assert status.lane_state == "active_exact"
    assert status.wish_character_limit == 500
    assert status.timeline_entry_limit == 12


@pytest.mark.asyncio
async def test_handle_legacy_webhook_marks_vote_window_as_estimated() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="legacy-shared-secret"),
    )

    result = await service.handle_webhook(
        headers={"authorization": "legacy-shared-secret"},
        raw_body=json.dumps(
            {
                "bot": "1485920716573380660",
                "user": "222",
                "type": "upvote",
                "isWeekend": True,
            }
        ).encode("utf-8"),
        now_utc=datetime(2026, 4, 24, 12, tzinfo=UTC),
    )
    status = await service.get_vote_bonus_status(
        222,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )

    assert result.outcome == "processed"
    assert status.lane_state == "active_estimated"
    assert status.timing_source == "legacy_estimated"
    assert status.weight == 2


@pytest.mark.asyncio
async def test_enabling_vote_reminders_schedules_one_early_nudge_for_exact_window() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload()
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())
    await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=timestamp,
                payload=payload,
            )
        },
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )

    result = await service.set_vote_reminders_enabled(
        222,
        enabled=True,
        now_utc=datetime(2026, 4, 24, 12, 5, tzinfo=UTC),
    )

    reminder = repository.reminders[222]
    assert result.status.reminders_enabled is True
    assert result.status.reminder_lane_state == "armed_exact"
    assert reminder.scheduled_vote_expires_at == datetime(2026, 4, 25, 0, tzinfo=UTC)
    assert reminder.scheduled_reminder_at == datetime(2026, 4, 24, 23, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_enabling_vote_reminders_with_less_than_five_minutes_left_waits_for_next_vote() -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    repository.receipts["manual"] = SimpleNamespace(
        event_id="manual",
        discord_user_id=222,
        event_type="vote.create",
        webhook_mode="v2",
        payload_hash="a" * 64,
        trace_id=None,
        signature_timestamp=None,
        vote_created_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        vote_expires_at=datetime(2026, 4, 24, 12, 4, tzinfo=UTC),
        timing_source="exact",
        weight=1,
        received_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        processed_at=datetime(2026, 4, 24, 12, tzinfo=UTC),
        status="processed",
    )

    result = await service.set_vote_reminders_enabled(
        222,
        enabled=True,
        now_utc=datetime(2026, 4, 24, 12, 0, 30, tzinfo=UTC),
    )

    assert result.status.reminders_enabled is True
    assert result.status.reminder_lane_state == "waiting_for_next_vote"
    assert repository.reminders[222].scheduled_reminder_at is None


@pytest.mark.asyncio
async def test_duplicate_vote_delivery_reschedules_instead_of_duplicating_reminder_windows() -> None:
    repository = FakeVoteRepository()
    repository.reminders[222] = FakeReminderRecord(
        discord_user_id=222,
        enabled=True,
        scheduled_vote_expires_at=None,
        scheduled_reminder_at=None,
    )
    service = VoteService(
        repository,
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )
    payload = _v2_vote_payload(
        event_id="vote-1",
        created_at="2026-04-24T12:00:00+00:00",
        expires_at="2026-04-25T00:00:00+00:00",
    )
    timestamp = int(datetime(2026, 4, 24, 12, tzinfo=UTC).timestamp())
    await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=timestamp,
                payload=payload,
            )
        },
        raw_body=payload,
        now_utc=datetime(2026, 4, 24, 12, 1, tzinfo=UTC),
    )
    first_reminder_at = repository.reminders[222].scheduled_reminder_at

    refreshed_payload = _v2_vote_payload(
        event_id="vote-2",
        created_at="2026-04-24T14:00:00+00:00",
        expires_at="2026-04-25T02:00:00+00:00",
    )
    refreshed_timestamp = int(datetime(2026, 4, 24, 14, tzinfo=UTC).timestamp())
    await service.handle_webhook(
        headers={
            "x-topgg-signature": _v2_signature(
                "whs_test_secret",
                timestamp=refreshed_timestamp,
                payload=refreshed_payload,
            )
        },
        raw_body=refreshed_payload,
        now_utc=datetime(2026, 4, 24, 14, 1, tzinfo=UTC),
    )

    reminder = repository.reminders[222]
    assert first_reminder_at == datetime(2026, 4, 24, 23, 30, tzinfo=UTC)
    assert reminder.scheduled_vote_expires_at == datetime(2026, 4, 25, 2, tzinfo=UTC)
    assert reminder.scheduled_reminder_at == datetime(2026, 4, 25, 1, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_refresh_not_found_clears_pending_vote_reminder_for_current_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeVoteRepository()
    repository.reminders[222] = FakeReminderRecord(
        discord_user_id=222,
        enabled=True,
        scheduled_vote_expires_at=datetime(2026, 4, 25, 0, tzinfo=UTC),
        scheduled_reminder_at=datetime(2026, 4, 24, 23, 30, tzinfo=UTC),
        timing_source="exact",
    )
    service = VoteService(
        repository,
        settings=_settings(
            enabled=True,
            secret="whs_test_secret",
            token="topgg-api-token",
        ),
    )

    async def fake_fetch(_: int) -> dict[str, object] | None:
        return None

    monkeypatch.setattr(service, "_fetch_vote_status_by_user", fake_fetch)

    result = await service.refresh_vote_status(
        222,
        now_utc=datetime(2026, 4, 24, 12, tzinfo=UTC),
    )

    assert result.outcome == "not_found"
    assert repository.reminders[222].enabled is True
    assert repository.reminders[222].scheduled_reminder_at is None
    assert result.status.reminder_lane_state == "waiting_for_next_vote"


@pytest.mark.asyncio
async def test_refresh_vote_status_uses_exact_v1_response_and_enforces_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeVoteRepository()
    service = VoteService(
        repository,
        settings=_settings(
            enabled=True,
            secret="whs_test_secret",
            token="topgg-api-token",
            cooldown_seconds=60,
        ),
    )

    async def fake_fetch(user_id: int) -> dict[str, object]:
        assert user_id == 222
        return {
            "created_at": "2026-04-24T12:00:00+00:00",
            "expires_at": "2026-04-25T00:00:00+00:00",
            "weight": 1,
        }

    monkeypatch.setattr(service, "_fetch_vote_status_by_user", fake_fetch)

    refreshed = await service.refresh_vote_status(
        222,
        now_utc=datetime(2026, 4, 24, 12, tzinfo=UTC),
    )
    throttled = await service.refresh_vote_status(
        222,
        now_utc=datetime(2026, 4, 24, 12, 0, 30, tzinfo=UTC),
    )

    assert refreshed.outcome == "refreshed"
    assert refreshed.status.lane_state == "active_exact"
    assert throttled.outcome == "cooldown"
    assert throttled.status.refresh_retry_after_seconds == 30


@pytest.mark.asyncio
async def test_diagnostics_snapshot_uses_truthful_token_missing_copy() -> None:
    service = VoteService(
        FakeVoteRepository(),
        settings=_settings(enabled=True, secret="whs_test_secret"),
    )

    snapshot = service.diagnostics_snapshot()

    assert snapshot["configuration_state"] == "ready"
    assert "webhooks are active" in str(snapshot["configuration_message"]).lower()
    assert "optional manual refresh" in str(snapshot["configuration_message"]).lower()
