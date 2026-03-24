from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

CelebrationMode = Literal["quiet", "party"]
AnnouncementTheme = Literal["classic", "festive", "minimal", "cute", "elegant", "gaming"]
ProfileVisibility = Literal["private", "server_visible"]
CelebrationKind = Literal["custom", "server_anniversary"]
AnnouncementKind = Literal[
    "birthday_announcement",
    "birthday_dm",
    "anniversary",
    "server_anniversary",
    "recurring_event",
]
EventKind = Literal[
    "announcement",
    "birthday_dm",
    "anniversary_announcement",
    "recurring_announcement",
    "role_start",
    "role_end",
]
EventState = Literal["pending", "processing", "completed"]
HealthSeverity = Literal["info", "warning", "error"]
AnnouncementBatchState = Literal["pending", "sending", "sent"]
AnnouncementBatchClaimStatus = Literal["missing", "claimed", "already_sent", "in_flight"]
AnnouncementDeliveryStatus = Literal["ready", "blocked"]
DiagnosticSeverity = Literal["info", "warning", "error"]


@dataclass(slots=True, frozen=True)
class AnnouncementStudioPresentation:
    theme: AnnouncementTheme
    title_override: str | None
    footer_text: str | None
    image_url: str | None
    thumbnail_url: str | None
    accent_color: int | None


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
    announcement_title_override: str | None
    announcement_footer_text: str | None
    announcement_image_url: str | None
    announcement_thumbnail_url: str | None
    announcement_accent_color: int | None
    birthday_dm_enabled: bool
    birthday_dm_template: str | None
    anniversary_enabled: bool
    anniversary_channel_id: int | None
    anniversary_template: str | None
    eligibility_role_id: int | None
    ignore_bots: bool
    minimum_membership_days: int
    mention_suppression_threshold: int
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
            announcement_title_override=None,
            announcement_footer_text=None,
            announcement_image_url=None,
            announcement_thumbnail_url=None,
            announcement_accent_color=None,
            birthday_dm_enabled=False,
            birthday_dm_template=None,
            anniversary_enabled=False,
            anniversary_channel_id=None,
            anniversary_template=None,
            eligibility_role_id=None,
            ignore_bots=True,
            minimum_membership_days=0,
            mention_suppression_threshold=8,
        )

    def presentation(self) -> AnnouncementStudioPresentation:
        return AnnouncementStudioPresentation(
            theme=self.announcement_theme,
            title_override=self.announcement_title_override,
            footer_text=self.announcement_footer_text,
            image_url=self.announcement_image_url,
            thumbnail_url=self.announcement_thumbnail_url,
            accent_color=self.announcement_accent_color,
        )


@dataclass(slots=True, frozen=True)
class MemberBirthday:
    guild_id: int
    user_id: int
    birth_month: int
    birth_day: int
    birth_year: int | None
    timezone_override: str | None
    profile_visibility: ProfileVisibility
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
class TrackedAnniversary:
    guild_id: int
    user_id: int
    joined_at_utc: datetime
    next_occurrence_at_utc: datetime
    source: str
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None


@dataclass(slots=True, frozen=True)
class RecurringCelebration:
    id: int
    guild_id: int
    name: str
    event_month: int
    event_day: int
    channel_id: int | None
    template: str | None
    enabled: bool
    next_occurrence_at_utc: datetime
    celebration_kind: CelebrationKind = "custom"
    use_guild_created_date: bool = False
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None


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
    profile_visibility: ProfileVisibility


@dataclass(slots=True, frozen=True)
class AnnouncementRecipientSnapshot:
    user_id: int
    birth_month: int
    birth_day: int
    timezone: str


@dataclass(slots=True, frozen=True)
class AnniversaryRecipientSnapshot:
    user_id: int
    joined_at_utc: datetime


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
class DeliveryDiagnostic:
    severity: DiagnosticSeverity
    code: str
    summary: str
    action: str | None = None

    def detail_line(self) -> str:
        if self.action:
            return f"{self.summary}\nAction: {self.action}"
        return self.summary


@dataclass(slots=True, frozen=True)
class AnnouncementDeliveryReadiness:
    status: AnnouncementDeliveryStatus
    summary: str
    details: tuple[str, ...] = ()
    diagnostics: tuple[DeliveryDiagnostic, ...] = ()


@dataclass(slots=True, frozen=True)
class HealthIssue:
    severity: HealthSeverity
    code: str
    summary: str
    action: str


@dataclass(slots=True, frozen=True)
class SchedulerBacklog:
    oldest_due_birthday_utc: datetime | None
    oldest_due_anniversary_utc: datetime | None
    oldest_due_recurring_utc: datetime | None
    oldest_due_role_removal_utc: datetime | None
    oldest_due_event_utc: datetime | None
    stale_processing_count: int


@dataclass(slots=True, frozen=True)
class RecentDeliveryIssue:
    event_kind: EventKind
    scheduled_for_utc: datetime
    completed_at_utc: datetime | None
    last_error_code: str | None
    message_id: int | None


@dataclass(slots=True, frozen=True)
class BirthdayImportRow:
    row_number: int
    user_id: int
    birth_month: int
    birth_day: int
    birth_year: int | None
    timezone_override: str | None
    profile_visibility: ProfileVisibility


@dataclass(slots=True, frozen=True)
class BirthdayImportError:
    row_number: int
    message: str


@dataclass(slots=True, frozen=True)
class BirthdayImportPreview:
    total_rows: int
    valid_rows: tuple[BirthdayImportRow, ...]
    errors: tuple[BirthdayImportError, ...]
    apply_token: str


@dataclass(slots=True)
class SchedulerMetrics:
    last_iteration_at_utc: datetime | None = None
    last_success_at_utc: datetime | None = None
    last_error_code: str | None = None
    recovery_completed: bool = False
    iterations: int = 0
    last_claimed_events: int = 0
    recent_errors: list[str] = field(default_factory=list)
