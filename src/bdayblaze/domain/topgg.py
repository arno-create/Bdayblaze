from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

TopggWebhookMode = Literal["v2", "legacy"]
VoteTimingSource = Literal["exact", "legacy_estimated"]
VoteLaneState = Literal[
    "disabled",
    "misconfigured",
    "inactive",
    "active_exact",
    "active_estimated",
]
VoteReminderLaneState = Literal[
    "off",
    "armed_exact",
    "armed_estimated",
    "waiting_for_next_vote",
    "delivery_issue",
]
TopggReceiptStatus = Literal["processed", "ignored_test"]
TopggWebhookOutcome = Literal[
    "processed",
    "duplicate",
    "ignored_test",
    "disabled",
    "invalid_signature",
    "stale",
    "invalid_payload",
    "misconfigured",
]
VoteRefreshOutcome = Literal["refreshed", "cooldown", "unavailable", "not_found"]

V2_SECRET_PREFIX = "whs_"


@dataclass(slots=True, frozen=True)
class TopggVoteReceipt:
    event_id: str
    discord_user_id: int
    event_type: str
    webhook_mode: TopggWebhookMode
    payload_hash: str
    trace_id: str | None
    signature_timestamp: datetime | None
    vote_created_at: datetime | None
    vote_expires_at: datetime | None
    timing_source: VoteTimingSource | None
    weight: int
    received_at: datetime
    processed_at: datetime
    status: TopggReceiptStatus
    error_text: str | None = None


@dataclass(slots=True, frozen=True)
class VoteBonusStatus:
    lane_state: VoteLaneState
    enabled: bool
    active: bool
    configuration_message: str | None
    voted_at_utc: datetime | None
    expires_at_utc: datetime | None
    timing_source: VoteTimingSource | None
    weight: int | None
    refresh_available: bool
    refresh_cooldown_seconds: int
    refresh_retry_after_seconds: int | None
    wish_character_limit: int
    timeline_entry_limit: int
    reminders_enabled: bool
    reminder_lane_state: VoteReminderLaneState
    next_reminder_at_utc: datetime | None
    last_reminder_error_code: str | None
    reminder_timing_source: VoteTimingSource | None


@dataclass(slots=True, frozen=True)
class TopggVoteReminder:
    discord_user_id: int
    enabled: bool
    scheduled_vote_expires_at: datetime | None
    scheduled_reminder_at: datetime | None
    processing_started_at: datetime | None
    last_reminded_vote_expires_at: datetime | None
    last_reminded_at: datetime | None
    attempt_count: int
    last_error_code: str | None
    timing_source: VoteTimingSource | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class TopggWebhookResult:
    http_status: int
    outcome: TopggWebhookOutcome
    payload: dict[str, object]
    receipt: TopggVoteReceipt | None = None


@dataclass(slots=True, frozen=True)
class VoteRefreshResult:
    outcome: VoteRefreshOutcome
    status: VoteBonusStatus
    note: str


@dataclass(slots=True, frozen=True)
class VoteReminderUpdateResult:
    status: VoteBonusStatus
    note: str


def is_v2_webhook_secret(secret: str) -> bool:
    return secret.startswith(V2_SECRET_PREFIX)


def payload_sha256(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def build_v2_signature(secret: str, *, timestamp: int, payload: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()


def parse_signature_header(header_value: str | None) -> tuple[int, str] | None:
    if not header_value:
        return None
    timestamp: int | None = None
    signature: str | None = None
    for part in header_value.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None
        elif key == "v1":
            signature = value.strip().lower()
    if timestamp is None or not signature:
        return None
    return timestamp, signature


def parse_topgg_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
