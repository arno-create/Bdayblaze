from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

CelebrationMode = Literal["quiet", "party"]
AnnouncementTheme = Literal["classic", "festive", "minimal", "cute"]
EventKind = Literal["announcement", "role_start", "role_end"]
EventState = Literal["pending", "processing", "completed"]
HealthSeverity = Literal["info", "warning", "error"]
AnnouncementBatchState = Literal["pending", "sending", "sent"]
AnnouncementBatchClaimStatus = Literal["missing", "claimed", "already_sent", "in_flight"]
AnnouncementDeliveryStatus = Literal["ready", "blocked"]


@dataclass(slots=True, frozen=True)
class GuildSettings:
    guild_id: int
    announcement_channel_id: int | None
    default_timezone: str
    birthday_role_id: int | None
    announcements_enabled: bool
    role_enabled: bool
    celebration_mode: CelebrationMode
    announcement_theme: AnnouncementTheme
    announcement_template: str | None
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None

    @classmethod
    def default(cls, guild_id: int) -> GuildSettings:
        return cls(
            guild_id=guild_id,
            announcement_channel_id=None,
            default_timezone="UTC",
            birthday_role_id=None,
            announcements_enabled=False,
            role_enabled=False,
            celebration_mode="quiet",
            announcement_theme="classic",
            announcement_template=None,
        )


@dataclass(slots=True, frozen=True)
class MemberBirthday:
    guild_id: int
    user_id: int
    birth_month: int
    birth_day: int
    birth_year: int | None
    timezone_override: str | None
    age_visible: bool
    next_occurrence_at_utc: datetime
    next_role_removal_at_utc: datetime | None
    active_birthday_role_id: int | None
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None

    def effective_timezone(self, settings: GuildSettings | None) -> str:
        if self.timezone_override:
            return self.timezone_override
        if settings is not None:
            return settings.default_timezone
        return "UTC"


@dataclass(slots=True, frozen=True)
class CelebrationEvent:
    id: int
    event_key: str
    guild_id: int
    user_id: int | None
    event_kind: EventKind
    scheduled_for_utc: datetime
    state: EventState
    payload: dict[str, Any]
    attempt_count: int
    last_error_code: str | None
    message_id: int | None
    created_at_utc: datetime
    updated_at_utc: datetime
    completed_at_utc: datetime | None
    processing_started_at_utc: datetime | None


@dataclass(slots=True, frozen=True)
class BirthdayPreview:
    user_id: int
    birth_month: int
    birth_day: int
    next_occurrence_at_utc: datetime
    effective_timezone: str


@dataclass(slots=True, frozen=True)
class AnnouncementRecipientSnapshot:
    user_id: int
    birth_month: int
    birth_day: int
    timezone: str


@dataclass(slots=True, frozen=True)
class AnnouncementBatch:
    batch_token: str
    guild_id: int
    channel_id: int
    scheduled_for_utc: datetime
    state: AnnouncementBatchState
    message_id: int | None
    send_started_at_utc: datetime | None
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(slots=True, frozen=True)
class AnnouncementBatchClaim:
    status: AnnouncementBatchClaimStatus
    batch: AnnouncementBatch | None
    needs_history_check: bool = False


@dataclass(slots=True, frozen=True)
class AnnouncementDeliveryReadiness:
    status: AnnouncementDeliveryStatus
    summary: str
    details: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class HealthIssue:
    severity: HealthSeverity
    code: str
    summary: str
    action: str


@dataclass(slots=True, frozen=True)
class SchedulerBacklog:
    oldest_due_birthday_utc: datetime | None
    oldest_due_role_removal_utc: datetime | None
    oldest_due_event_utc: datetime | None
    stale_processing_count: int


@dataclass(slots=True)
class SchedulerMetrics:
    last_iteration_at_utc: datetime | None = None
    last_success_at_utc: datetime | None = None
    last_error_code: str | None = None
    recovery_completed: bool = False
    iterations: int = 0
    last_claimed_events: int = 0
    recent_errors: list[str] = field(default_factory=list)
