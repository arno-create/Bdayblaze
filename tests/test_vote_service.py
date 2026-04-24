from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from bdayblaze.config import Settings
from bdayblaze.services.vote_service import VoteService


class FakeVoteRepository:
    def __init__(self) -> None:
        self.receipts: dict[str, object] = {}

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
