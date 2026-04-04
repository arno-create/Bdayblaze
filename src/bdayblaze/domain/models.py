from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

CelebrationMode = Literal["quiet", "party"]
AnnouncementTheme = Literal["classic", "festive", "minimal", "cute", "elegant", "gaming"]
ProfileVisibility = Literal["private", "server_visible"]
BirthdayDisplayStatus = Literal["active", "recovering", "upcoming"]
CelebrationKind = Literal["custom", "server_anniversary"]
WishState = Literal["queued", "revealed", "removed", "moderated"]
CapsuleState = Literal[
    "disabled",
    "no_wishes",
    "revealed_private",
    "pending_public",
    "posted_public",
]
SurpriseRewardType = Literal["featured", "badge", "custom_note", "nitro_concierge"]
NitroFulfillmentStatus = Literal["pending", "delivered", "not_delivered"]
AnnouncementKind = Literal[
    "birthday_announcement",
    "birthday_dm",
    "anniversary",
    "server_anniversary",
    "recurring_event",
]
AnnouncementSurfaceKind = Literal[
    "birthday_announcement",
    "anniversary",
    "server_anniversary",
    "recurring_event",
]
EventKind = Literal[
    "announcement",
    "birthday_dm",
    "anniversary_announcement",
    "recurring_announcement",
    "capsule_reveal",
    "role_start",
    "role_end",
]
EventState = Literal["pending", "processing", "completed"]
HealthSeverity = Literal["info", "warning", "error"]
AnnouncementBatchState = Literal["pending", "sending", "sent"]
AnnouncementBatchClaimStatus = Literal["missing", "claimed", "already_sent", "in_flight"]
AnnouncementDeliveryStatus = Literal["ready", "blocked"]
DiagnosticSeverity = Literal["info", "warning", "error"]
T = TypeVar("T")


@dataclass(slots=True, frozen=True)
class AnnouncementStudioPresentation:
    theme: AnnouncementTheme
    title_override: str | None
    footer_text: str | None
    image_url: str | None
    thumbnail_url: str | None
    accent_color: int | None


@dataclass(slots=True, frozen=True)
class AnnouncementSurfaceSettings:
    guild_id: int
    surface_kind: AnnouncementSurfaceKind
    channel_id: int | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None

    @classmethod
    def empty(
        cls,
        guild_id: int,
        surface_kind: AnnouncementSurfaceKind,
    ) -> AnnouncementSurfaceSettings:
        return cls(guild_id=guild_id, surface_kind=surface_kind)


@dataclass(slots=True, frozen=True)
class ResolvedSurfaceField(Generic[T]):
    configured_value: T | None
    effective_value: T | None
    source: str
    override_value: T | None = None


@dataclass(slots=True, frozen=True)
class ResolvedAnnouncementSurface:
    surface_kind: AnnouncementSurfaceKind
    channel: ResolvedSurfaceField[int]
    image: ResolvedSurfaceField[str]
    thumbnail: ResolvedSurfaceField[str]

    def presentation(self, settings: GuildSettings) -> AnnouncementStudioPresentation:
        return settings.presentation_for_kind(
            self.surface_kind,
            image_url=self.image.effective_value,
            thumbnail_url=self.thumbnail.effective_value,
        )


@dataclass(slots=True, frozen=True)
class GuildSettings:
    guild_id: int
    default_timezone: str
    birthday_role_id: int | None
    announcements_enabled: bool
    role_enabled: bool
    celebration_mode: CelebrationMode
    announcement_theme: AnnouncementTheme
    announcement_template: str | None
    announcement_title_override: str | None
    announcement_footer_text: str | None
    announcement_accent_color: int | None
    birthday_dm_enabled: bool
    birthday_dm_template: str | None
    anniversary_enabled: bool
    anniversary_template: str | None
    eligibility_role_id: int | None
    ignore_bots: bool
    minimum_membership_days: int
    mention_suppression_threshold: int
    studio_audit_channel_id: int | None = None
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None

    @classmethod
    def default(cls, guild_id: int) -> GuildSettings:
        return cls(
            guild_id=guild_id,
            default_timezone="UTC",
            birthday_role_id=None,
            announcements_enabled=False,
            role_enabled=False,
            celebration_mode="quiet",
            announcement_theme="classic",
            announcement_template=None,
            announcement_title_override=None,
            announcement_footer_text=None,
            announcement_accent_color=None,
            birthday_dm_enabled=False,
            birthday_dm_template=None,
            anniversary_enabled=False,
            anniversary_template=None,
            eligibility_role_id=None,
            ignore_bots=True,
            minimum_membership_days=0,
            mention_suppression_threshold=8,
            studio_audit_channel_id=None,
        )

    def presentation(
        self,
        *,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
    ) -> AnnouncementStudioPresentation:
        return AnnouncementStudioPresentation(
            theme=self.announcement_theme,
            title_override=self.announcement_title_override,
            footer_text=self.announcement_footer_text,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
            accent_color=self.announcement_accent_color,
        )

    def presentation_for_kind(
        self,
        kind: AnnouncementKind,
        *,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
    ) -> AnnouncementStudioPresentation:
        presentation = self.presentation(
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        )
        if kind != "birthday_dm":
            return presentation
        return AnnouncementStudioPresentation(
            theme=presentation.theme,
            title_override=None,
            footer_text=None,
            image_url=None,
            thumbnail_url=None,
            accent_color=None,
        )


@dataclass(slots=True, frozen=True)
class GuildExperienceSettings:
    guild_id: int
    capsules_enabled: bool
    quests_enabled: bool
    quest_wish_target: int
    quest_reaction_target: int
    quest_checkin_enabled: bool
    surprises_enabled: bool
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None

    @classmethod
    def default(cls, guild_id: int) -> GuildExperienceSettings:
        return cls(
            guild_id=guild_id,
            capsules_enabled=False,
            quests_enabled=False,
            quest_wish_target=3,
            quest_reaction_target=5,
            quest_checkin_enabled=True,
            surprises_enabled=False,
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
class BirthdayWish:
    id: int
    guild_id: int
    author_user_id: int
    target_user_id: int
    wish_text: str
    link_url: str | None
    state: WishState
    celebration_occurrence_at_utc: datetime | None
    revealed_at_utc: datetime | None
    removed_at_utc: datetime | None
    moderated_by_user_id: int | None
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(slots=True, frozen=True)
class GuildSurpriseReward:
    id: int | None
    guild_id: int
    reward_type: SurpriseRewardType
    label: str
    weight: int
    enabled: bool
    note_text: str | None
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None


@dataclass(slots=True, frozen=True)
class BirthdayCelebration:
    id: int
    guild_id: int
    user_id: int
    occurrence_start_at_utc: datetime
    late_delivery: bool
    announcement_message_id: int | None
    capsule_state: CapsuleState
    capsule_message_id: int | None
    revealed_wish_count: int
    quest_enabled: bool
    quest_wish_target: int
    quest_wish_goal_met: bool
    quest_reaction_target: int
    quest_reaction_count: int
    quest_reaction_goal_met: bool
    quest_checkin_required: bool
    quest_checked_in_at_utc: datetime | None
    quest_completed_at_utc: datetime | None
    featured_birthday: bool
    surprise_reward_type: SurpriseRewardType | None
    surprise_reward_label: str | None
    surprise_note_text: str | None
    surprise_selected_at_utc: datetime | None
    nitro_fulfillment_status: NitroFulfillmentStatus | None
    nitro_fulfilled_by_user_id: int | None
    nitro_fulfilled_at_utc: datetime | None
    created_at_utc: datetime
    updated_at_utc: datetime


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
class BirthdayDisplayState:
    status: BirthdayDisplayStatus
    relevant_occurrence_at_utc: datetime
    next_future_occurrence_at_utc: datetime
    celebration_ends_at_utc: datetime | None = None


@dataclass(slots=True, frozen=True)
class BirthdayBrowseEntry:
    preview: BirthdayPreview
    display_state: BirthdayDisplayState


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
    last_activity_at_utc: datetime | None = None
    last_error_code: str | None = None
    recovery_completed: bool = False
    iterations: int = 0
    last_claimed_events: int = 0
    recent_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeStatus:
    process_started_at_utc: datetime
    db_pool_ready_at_utc: datetime | None = None
    migrations_started_at_utc: datetime | None = None
    migrations_completed_at_utc: datetime | None = None
    migrations_failed_at_utc: datetime | None = None
    health_server_started_at_utc: datetime | None = None
    health_server_failed_at_utc: datetime | None = None
    bot_login_started_at_utc: datetime | None = None
    bot_ready_at_utc: datetime | None = None
    scheduler_recovery_started_at_utc: datetime | None = None
    scheduler_recovery_completed_at_utc: datetime | None = None
    scheduler_recovery_failed_at_utc: datetime | None = None
    unexpected_shutdown_at_utc: datetime | None = None
    startup_phase: str = "starting"


@dataclass(slots=True, frozen=True)
class TimelineEntry:
    celebration_id: int
    occurrence_start_at_utc: datetime
    late_delivery: bool
    revealed_wish_count: int
    quest_completed: bool
    featured_birthday: bool
    surprise_reward_type: SurpriseRewardType | None
    surprise_reward_label: str | None
    nitro_fulfillment_status: NitroFulfillmentStatus | None


@dataclass(slots=True, frozen=True)
class BirthdayTimeline:
    birthday: MemberBirthday
    active_celebration: BirthdayCelebration | None
    display_state: BirthdayDisplayState
    celebration_count: int
    celebration_streak: int
    wishes_received_count: int
    quest_badge_count: int
    surprise_count: int
    featured_count: int
    next_countdown_at_utc: datetime
    same_day_count: int
    month_total_count: int
    zodiac_label: str | None
    entries: tuple[TimelineEntry, ...]


@dataclass(slots=True, frozen=True)
class BirthdayQuestStatus:
    celebration: BirthdayCelebration | None
    settings: GuildExperienceSettings
    can_check_in: bool


@dataclass(slots=True, frozen=True)
class NitroConciergeEntry:
    celebration_id: int
    user_id: int
    occurrence_start_at_utc: datetime
    reward_label: str
    note_text: str | None
    fulfillment_status: NitroFulfillmentStatus


@dataclass(slots=True, frozen=True)
class GuildAnalytics:
    birthdays_total: int
    birthdays_private: int
    birthdays_visible: int
    wishes_queued: int
    wishes_revealed: int
    celebrations_total: int
    quest_completions: int
    surprises_total: int
    nitro_pending: int
    nitro_delivered: int
    nitro_not_delivered: int
    anniversaries_tracked: int
    recurring_events_total: int
    most_active_month: int | None
    most_active_month_count: int
    recent_late_recoveries: int
    recent_scheduler_issues: int
