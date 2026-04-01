from __future__ import annotations

import asyncio
from calendar import month_name, monthrange
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, cast

import discord

from bdayblaze.discord.announcements import build_announcement_message
from bdayblaze.discord.embed_budget import BudgetedEmbed, code_block_snippet, truncate_text
from bdayblaze.domain.announcement_surfaces import (
    normalize_announcement_surfaces,
    resolve_announcement_surface,
    surface_label,
)
from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNIVERSARY_TEMPLATE,
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    DEFAULT_DM_TEMPLATE,
    default_template_for_kind,
    preview_context_for_kind,
    server_anniversary_years_since_creation,
    supported_placeholder_groups,
)
from bdayblaze.domain.announcement_theme import (
    announcement_theme_description,
    announcement_theme_label,
    supported_announcement_themes,
)
from bdayblaze.domain.media_validation import (
    assess_media_url,
    mark_validated_direct_media_url,
    strip_validated_direct_media_marker,
)
from bdayblaze.domain.models import (
    AnnouncementKind,
    AnnouncementStudioPresentation,
    AnnouncementSurfaceKind,
    AnnouncementSurfaceSettings,
    GuildExperienceSettings,
    GuildSettings,
    GuildSurpriseReward,
    RecurringCelebration,
    ResolvedAnnouncementSurface,
    ResolvedSurfaceField,
)
from bdayblaze.domain.operator_summary import (
    celebration_mode_summary,
    media_health_line,
    media_line,
    media_source_line,
    route_line,
    route_source_line,
)
from bdayblaze.domain.timezones import timezone_guidance
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.content_policy import ContentPolicyError, ensure_safe_announcement_inputs
from bdayblaze.services.diagnostics import (
    build_presentation_diagnostics,
    classify_discord_http_failure,
)
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.experience_service import ExperienceService
from bdayblaze.services.media_validation_service import MediaProbeResult, probe_media_url
from bdayblaze.services.settings_service import SettingsService

_SETUP_TITLE: Final = "\U0001F6E0 Birthday Setup"
_STUDIO_TITLE: Final = "\u2728 Celebration Studio"
_UI_UNSET: Final = object()
_UI_LOGGER = get_logger(component="celebration_studio")
SectionName = Literal[
    "home",
    "birthday",
    "birthday_dm",
    "anniversary",
    "server_anniversary",
    "capsules",
    "quests",
    "surprises",
    "events",
    "help",
]
_SECTION_LABELS: Final[dict[SectionName, str]] = {
    "home": "\u2728 Studio overview",
    "birthday": "\U0001F382 Birthday announcement",
    "birthday_dm": "\U0001F48C Birthday DM",
    "anniversary": "\U0001F389 Member anniversary",
    "server_anniversary": "\U0001F3F0 Server anniversary",
    "capsules": "\u2709\ufe0f Birthday Capsules",
    "quests": "\U0001F3AF Birthday Quests",
    "surprises": "\U0001F381 Birthday Surprises",
    "events": "\U0001F4C5 Custom annual events",
    "help": "\U0001F9ED Studio help",
}
_SECTION_SURFACE_KIND: Final[dict[SectionName, AnnouncementSurfaceKind | None]] = {
    "home": "birthday_announcement",
    "birthday": "birthday_announcement",
    "birthday_dm": None,
    "anniversary": "anniversary",
    "server_anniversary": "server_anniversary",
    "capsules": None,
    "quests": None,
    "surprises": None,
    "events": "recurring_event",
    "help": None,
}
_PREVIEW_SURFACES: Final[tuple[AnnouncementKind, ...]] = (
    "birthday_announcement",
    "birthday_dm",
    "anniversary",
    "server_anniversary",
    "recurring_event",
)

@dataclass(slots=True, frozen=True)
class ServerAnniversaryState:
    enabled: bool
    name: str
    month: int | None
    day: int | None
    channel_id: int | None
    template: str | None
    use_guild_created_date: bool
    exists: bool


def _normalized_surfaces(
    settings: GuildSettings,
    announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None,
) -> dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings]:
    return normalize_announcement_surfaces(settings.guild_id, announcement_surfaces or {})


def _resolve_surface(
    settings: GuildSettings,
    announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None,
    surface_kind: AnnouncementSurfaceKind,
    *,
    event_channel_id: int | None = None,
) -> ResolvedAnnouncementSurface:
    return resolve_announcement_surface(
        settings.guild_id,
        surface_kind,
        _normalized_surfaces(settings, announcement_surfaces),
        event_channel_id=event_channel_id,
    )


def _surface_route_lines(surface: ResolvedAnnouncementSurface) -> tuple[str, str]:
    return (
        route_line(surface.channel, surface_kind=surface.surface_kind),
        route_source_line(surface.channel, surface_kind=surface.surface_kind),
    )


def _surface_media_lines(surface: ResolvedAnnouncementSurface) -> tuple[str, ...]:
    return (
        media_line(surface.image, label="image", surface_kind=surface.surface_kind),
        media_line(surface.thumbnail, label="thumbnail", surface_kind=surface.surface_kind),
        media_health_line(surface),
        media_source_line(surface),
    )


def _surface_media_field_lines(
    field: ResolvedSurfaceField[str],
    *,
    label: Literal["image", "thumbnail"],
    surface: ResolvedAnnouncementSurface,
) -> tuple[str, str]:
    return (
        media_line(field, label=label, surface_kind=surface.surface_kind),
        f"{label.title()} source: {media_source_line(surface).split(': ', 1)[1]}",
    )


def _media_value_label(
    value: str,
    *,
    label: str,
    max_length: int = 72,
) -> str:
    assessment = assess_media_url(value, label=label.title())
    if assessment is None:
        return "Not set"
    display_url = strip_validated_direct_media_marker(assessment.normalized_url)
    return f"{assessment.status_label()} | {truncate_text(display_url or '', max_length)}"


def _celebration_mode_label(mode: str) -> str:
    return celebration_mode_summary(mode)


def _default_preview_kind_for_section(section: SectionName) -> AnnouncementKind:
    preview_kinds: dict[SectionName, AnnouncementKind] = {
        "home": "birthday_announcement",
        "birthday": "birthday_announcement",
        "birthday_dm": "birthday_dm",
        "anniversary": "anniversary",
        "server_anniversary": "server_anniversary",
        "capsules": "birthday_announcement",
        "quests": "birthday_announcement",
        "surprises": "birthday_announcement",
        "events": "recurring_event",
        "help": "birthday_announcement",
    }
    return preview_kinds[section]


def _supports_preview_surface_selection(section: SectionName) -> bool:
    return section in {
        "home",
        "birthday",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "events",
    }


def _surface_media_button_label(section: SectionName) -> str:
    return {
        "home": "Birthday route/media",
        "birthday": "Birthday route/media",
        "anniversary": "Anniversary route/media",
        "server_anniversary": "Server route/media",
        "events": "Events route/media",
    }.get(section, "Surface route/media")


def _global_look_lines(settings: GuildSettings) -> tuple[str, ...]:
    return (
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        f"Theme note: {announcement_theme_description(settings.announcement_theme)}",
        f"Title override: {settings.announcement_title_override or 'Default'}",
        f"Footer text: {settings.announcement_footer_text or 'Default'}",
        f"Global celebration behavior: {_celebration_mode_label(settings.celebration_mode)}",
        f"Accent color: {_format_accent_color(settings.announcement_accent_color)}",
    )


def _style_summary_lines(settings: GuildSettings) -> tuple[str, ...]:
    return (
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        f"Mode: {_celebration_mode_label(settings.celebration_mode)}",
        f"Title: {settings.announcement_title_override or 'Default'}",
        f"Footer: {settings.announcement_footer_text or 'Default'}",
    )


async def _send_safe_ui_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    surface: str,
) -> None:
    guild_hash = (
        redact_identifier(interaction.guild_id) if interaction.guild_id is not None else None
    )
    user_hash = redact_identifier(interaction.user.id)
    is_admin = bool(
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.manage_guild
    )
    if isinstance(error, ValidationError):
        message = str(error)
    elif isinstance(error, discord.HTTPException):
        failure = classify_discord_http_failure(error, surface="ui")
        _UI_LOGGER.warning(
            "celebration_studio_http_error",
            surface=surface,
            guild_id=guild_hash,
            user_id=user_hash,
            status=error.status,
            discord_code=error.code,
        )
        message = failure.summary
        if failure.action:
            message = f"{message}\nAction: {failure.action}"
        if is_admin and failure.permanent:
            message = f"{message}\nHint: `BDAY-UI-400`."
    else:
        _UI_LOGGER.exception(
            "celebration_studio_error",
            surface=surface,
            guild_id=guild_hash,
            user_id=user_hash,
            error_type=type(error).__name__,
        )
        message = "Something went wrong while handling that studio action."
        if is_admin:
            message = f"{message}\nHint: `BDAY-UNEXPECTED`."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

async def _audit_blocked_attempt(
    interaction: discord.Interaction,
    *,
    surface: str,
    error: ContentPolicyError,
) -> None:
    container = getattr(interaction.client, "container", None)
    studio_audit_logger = getattr(container, "studio_audit_logger", None)
    if studio_audit_logger is None:
        return
    await studio_audit_logger.log_blocked_attempt(
        interaction,
        surface=surface,
        error=error,
    )


async def _audit_blocked_media_attempt(
    interaction: discord.Interaction,
    *,
    surface: str,
    field_labels: tuple[str, ...],
) -> None:
    container = getattr(interaction.client, "container", None)
    studio_audit_logger = getattr(container, "studio_audit_logger", None)
    if studio_audit_logger is None:
        return
    await studio_audit_logger.log_blocked_fields(
        interaction,
        surface=surface,
        field_labels=field_labels,
        category_labels=("unsafe media URL",),
        rule_codes=("unsafe_media_url",),
    )


class AdminPanelView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _: discord.ui.Item[discord.ui.View],
    ) -> None:
        await _send_safe_ui_error(interaction, error, surface="component")


class AdminPanelModal(discord.ui.Modal):
    async def on_error(  # type: ignore[override]
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        await _send_safe_ui_error(interaction, error, surface="modal")


def build_setup_embed(
    settings: GuildSettings,
    announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None = None,
    note: str | None = None,
) -> discord.Embed:
    birthday_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "birthday_announcement",
    )
    anniversary_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "anniversary",
    )
    server_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "server_anniversary",
    )
    recurring_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "recurring_event",
    )
    budget = BudgetedEmbed.create(
        title=_SETUP_TITLE,
        description=(
            "Control live delivery basics first: routes, timezone, eligibility, and the guardrails "
            "that decide who gets celebrated and where it lands."
        ),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="\U0001F382 Birthday delivery",
        value="\n".join(
            (
                f"Live: {_format_enabled(settings.announcements_enabled)}",
                *_surface_route_lines(birthday_surface),
                *_surface_media_lines(birthday_surface)[:3],
            )
        ),
        inline=False,
    )
    budget.add_field(
        name="\U0001F48C Private birthday DM",
        value="\n".join(
            (
                f"Live: {_format_enabled(settings.birthday_dm_enabled)}",
                "Route: private DM only",
                "Media: not used for birthday DMs",
                f"Style: {_celebration_mode_label(settings.celebration_mode)}",
            )
        ),
        inline=False,
    )
    budget.add_field(
        name="\U0001F6E1 Roles and eligibility",
        value="\n".join(
            (
                "Birthday role: "
                f"{_format_enabled(settings.role_enabled)} "
                f"({_format_role(settings.birthday_role_id)})",
                f"Eligibility: {_format_eligibility_role(settings.eligibility_role_id)}",
                f"Ignore bots: {_format_enabled(settings.ignore_bots)}",
                f"Minimum membership age: {settings.minimum_membership_days} day(s)",
                f"Mention suppression threshold: {settings.mention_suppression_threshold}",
            )
        ),
        inline=False,
    )
    budget.add_field(
        name="\U0001F389 Member anniversaries",
        value="\n".join(
            (
                f"Live: {_format_enabled(settings.anniversary_enabled)}",
                *_surface_route_lines(anniversary_surface),
                *_surface_media_lines(anniversary_surface)[:3],
                "Audience: tracked members only",
            )
        ),
        inline=False,
    )
    budget.add_field(
        name="\U0001F4C5 Annual celebrations",
        value="\n".join(
            (
                "Server anniversary default:",
                f"- {_surface_route_lines(server_surface)[0]}",
                f"- {_surface_media_lines(server_surface)[0]}",
                f"- {_surface_media_lines(server_surface)[1]}",
                "Recurring events default:",
                f"- {_surface_route_lines(recurring_surface)[0]}",
                f"- {_surface_media_lines(recurring_surface)[0]}",
                f"- {_surface_media_lines(recurring_surface)[1]}",
                "Saved event-level channel overrides still win when one exists.",
            )
        ),
        inline=False,
    )
    budget.add_field(
        name="\U0001F30D Timezone and safety",
        value="\n".join(
            (
                f"Default timezone: `{settings.default_timezone}`",
                f"Studio audit log: {_format_channel(settings.studio_audit_channel_id)}",
                "Unsafe message text, event names, and media URLs are blocked before save.",
                f"Timezone examples: {timezone_guidance(allow_server_default=False)}",
            )
        ),
        inline=False,
    )
    budget.add_field(
        name="\u2728 Celebration Studio",
        value=(
            "Use Celebration Studio for copy, Quiet vs Party style, previews, media tools, "
            "server anniversary copy, capsules, quests, surprises, and recurring-event design."
        ),
        inline=False,
    )
    if note:
        budget.add_field(name="\u2728 Updated", value=note, inline=False)
    budget.set_footer(
        "Setup keeps delivery basics clean. Studio handles how celebrations look and feel."
    )
    return budget.build()

def build_timezone_help_embed(*, allow_server_default: bool) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Timezone help",
        description=timezone_guidance(allow_server_default=allow_server_default),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="What to enter",
        value=(
            "Use the full IANA timezone name.\n"
            "Examples: `Asia/Yerevan`, `Europe/London`, `Europe/Berlin`, "
            "`America/New_York`, `America/Los_Angeles`, `Asia/Tokyo`."
        ),
        inline=False,
    )
    return budget.build()


def build_membership_rules_embed(
    settings: GuildSettings,
    *,
    note: str | None = None,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Membership and anti-spam rules",
        description=(
            "Set the delivery guardrails that decide who is eligible for birthday and "
            "anniversary celebrations."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="Updated", value=note, inline=False)
    budget.add_line_fields(
        "Current rules",
        (
            f"Ignore bots: {_format_enabled(settings.ignore_bots)}",
            f"Minimum membership age: {settings.minimum_membership_days} day(s)",
            f"Mention suppression threshold: {settings.mention_suppression_threshold}",
        ),
        inline=False,
    )
    budget.add_field(
        name="Presets and custom values",
        value=(
            "Use the preset selects for common values. Choose the custom option only when your "
            "server needs a different number."
        ),
        inline=False,
    )
    budget.set_footer("These rules apply before a live celebration is delivered.")
    return budget.build()


def build_quest_settings_embed(
    settings: GuildExperienceSettings,
    *,
    note: str | None = None,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Birthday Quest controls",
        description=(
            "Tune the live quest rules with native controls instead of raw parser inputs."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="Updated", value=note, inline=False)
    budget.add_line_fields(
        "Current rules",
        (
            f"Live: {_format_enabled(settings.quests_enabled)}",
            f"Wish target: {settings.quest_wish_target}",
            f"Reaction target: {settings.quest_reaction_target}",
            f"Check-in required: {_format_enabled(settings.quest_checkin_enabled)}",
        ),
        inline=False,
    )
    budget.add_field(
        name="Tracked objectives",
        value=(
            "Birthday Quests can count unlocked wishes, reactions on the shared birthday post, "
            "and an optional manual check-in."
        ),
        inline=False,
    )
    return budget.build()


def build_studio_safety_embed(
    settings: GuildSettings,
    *,
    note: str | None = None,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Studio safety",
        description=(
            "Blocked content checks stay deterministic and private. Raw blocked content is "
            "never echoed back into logs."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="Updated", value=note, inline=False)
    budget.add_field(
        name="Audit channel",
        value=_format_channel(settings.studio_audit_channel_id),
        inline=False,
    )
    budget.add_field(
        name="What gets blocked",
        value=(
            "Profanity, NSFW terms, slurs, harassment-style wording, and unsafe media URLs."
        ),
        inline=False,
    )
    budget.add_field(
        name="What gets logged",
        value=(
            "Only the admin, surface, field names, and blocked category. "
            "No raw template text or raw media URLs are logged."
        ),
        inline=False,
    )
    budget.set_footer("Set a channel to enable audit logging, or clear it to keep logging off.")
    return budget.build()


def build_media_tools_embed(
    settings: GuildSettings,
    *,
    announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None = None,
    surface_kind: AnnouncementSurfaceKind = "birthday_announcement",
    note: str | None = None,
    image_probe: MediaProbeResult | None = None,
    thumbnail_probe: MediaProbeResult | None = None,
    checked_image_url: str | None = None,
    checked_thumbnail_url: str | None = None,
) -> discord.Embed:
    resolved_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        surface_kind,
    )
    surface_title = surface_label(surface_kind)
    budget = BudgetedEmbed.create(
        title="Media Tools",
        description=(
            f"Manage the live route and media for {surface_title}. This view leads with what is "
            "live now, then shows validation details only when they help."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="Updated", value=note, inline=False)
    budget.add_line_fields(
        "Live surface",
        (
            *_surface_route_lines(resolved_surface),
            *_surface_media_lines(resolved_surface)[:3],
        ),
        inline=False,
    )
    budget.add_line_fields(
        "Inheritance and defaults",
        (
            _surface_media_lines(resolved_surface)[3],
            (
                "Birthday announcement acts as the default route and media source whenever "
                "another surface leaves a field unset."
                if surface_kind != "birthday_announcement"
                else "Birthday announcement is the root default surface."
            ),
            (
                "Saved event channel overrides still win for recurring events and server "
                "anniversary posts."
                if surface_kind in {"server_anniversary", "recurring_event"}
                else "Clear one field to inherit just that field again."
            ),
        ),
        inline=False,
    )
    if (
        image_probe is not None
        or thumbnail_probe is not None
        or (checked_image_url is not None and checked_image_url.strip())
        or (checked_thumbnail_url is not None and checked_thumbnail_url.strip())
    ):
        budget.add_line_fields(
            "Latest validation",
            (
                _media_validation_line(
                    checked_image_url,
                    label=f"{surface_title} image",
                    probe=image_probe,
                ),
                _media_validation_line(
                    checked_thumbnail_url,
                    label=f"{surface_title} thumbnail",
                    probe=thumbnail_probe,
                ),
            ),
            inline=False,
        )
    budget.add_field(
        name="Save protection",
        value=(
            "Blocked saves never clear the currently saved media. Validation can fail without "
            "wiping the image or thumbnail that is already live."
        ),
        inline=False,
    )
    budget.add_field(
        name="Quick fixes",
        value=(
            "Tenor/Giphy: use the direct media file URL, not the page link.\n"
            "Google image results: wrapper links are webpages, not direct media files.\n"
            "Tip: copy the image or GIF address itself, not the browser page URL."
        ),
        inline=False,
    )
    budget.add_field(
        name="Actions",
        value=(
            "Edit media validates before save.\n"
            "Validate current re-checks the live image and thumbnail without changing them.\n"
            "Clear image or Clear thumbnail removes only that one override.\n"
            "Reset surface to inherited clears route, image, and thumbnail overrides together."
        ),
        inline=False,
    )
    return budget.build()


def build_message_template_embed(
    settings: GuildSettings,
    *,
    announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None = None,
    note: str | None = None,
    section: SectionName = "home",
    guild: discord.Guild | None = None,
    experience_settings: GuildExperienceSettings | None = None,
    surprise_rewards: tuple[GuildSurpriseReward, ...] = (),
    server_anniversary: RecurringCelebration | None = None,
    recurring_events: tuple[RecurringCelebration, ...] = (),
) -> discord.Embed:
    experience_state = experience_settings or GuildExperienceSettings.default(settings.guild_id)
    state = _server_anniversary_state(guild=guild, celebration=server_anniversary)
    birthday_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "birthday_announcement",
    )
    anniversary_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "anniversary",
    )
    recurring_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "recurring_event",
    )
    server_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "server_anniversary",
        event_channel_id=state.channel_id,
    )
    budget = BudgetedEmbed.create(
        title=(
            _STUDIO_TITLE
            if section == "home"
            else f"{_STUDIO_TITLE} | {_SECTION_LABELS[section]}"
        ),
        description=_section_description(section),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="\U0001F50D Current focus",
        value=_SECTION_LABELS[section],
        inline=False,
    )
    if note:
        budget.add_field(name="\u2728 Update", value=note, inline=False)

    if section == "home":
        budget.add_field(
            name="\U0001F382 Birthday announcement",
            value="\n".join(
                (
                    f"Live: {_format_enabled(settings.announcements_enabled)}",
                    _surface_route_lines(birthday_surface)[0],
                    _surface_media_lines(birthday_surface)[0],
                    _surface_media_lines(birthday_surface)[1],
                    _surface_media_lines(birthday_surface)[2],
                    "Copy: "
                    f"{len(settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE)} chars",
                )
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F48C Birthday DM",
            value="\n".join(
                (
                    f"Live: {_format_enabled(settings.birthday_dm_enabled)}",
                    "Delivery: best effort private DM",
                    f"Copy: {len(settings.birthday_dm_template or DEFAULT_DM_TEMPLATE)} chars",
                    f"Style: {_celebration_mode_label(settings.celebration_mode)}",
                )
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F389 Member anniversary",
            value="\n".join(
                (
                    f"Live: {_format_enabled(settings.anniversary_enabled)}",
                    _surface_route_lines(anniversary_surface)[0],
                    _surface_media_lines(anniversary_surface)[0],
                    _surface_media_lines(anniversary_surface)[1],
                    _surface_media_lines(anniversary_surface)[2],
                    "Audience: tracked members only",
                )
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F3A8 Celebration style",
            value="\n".join(_style_summary_lines(settings)),
            inline=False,
        )
        budget.add_field(
            name="\U0001F3F0 Annual celebrations",
            value="\n".join(
                (
                    "Server anniversary: "
                    f"{_format_enabled(state.enabled)} "
                    f"on {_format_month_day(state.month, state.day)}",
                    (
                        "Date source: guild creation date"
                        if state.use_guild_created_date
                        else "Date source: custom saved date"
                    ),
                    _surface_route_lines(server_surface)[0],
                    f"Recurring defaults: {_surface_route_lines(recurring_surface)[0]}",
                    f"Configured yearly events: {len(recurring_events)}",
                    (
                        _format_event_line(recurring_events[0])
                        if recurring_events
                        else "No custom annual events are configured yet."
                    ),
                )
            ),
            inline=False,
        )
        budget.add_field(
            name="\u2709\ufe0f Birthday Capsules",
            value="\n".join(
                (
                    f"Live: {_format_enabled(experience_state.capsules_enabled)}",
                    "Reveal: announcement channel when live, otherwise private unlock only",
                )
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F3AF Birthday Quests",
            value="\n".join(
                (
                    f"Live: {_format_enabled(experience_state.quests_enabled)}",
                    f"Wish target: {experience_state.quest_wish_target}",
                    f"Reaction target: {experience_state.quest_reaction_target}",
                    f"Check-in: {_format_enabled(experience_state.quest_checkin_enabled)}",
                )
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F381 Birthday Surprises",
            value="\n".join(
                (
                    f"Live: {_format_enabled(experience_state.surprises_enabled)}",
                    f"Enabled rewards: {_enabled_reward_count(surprise_rewards)}",
                    "Nitro stays manual-only concierge fulfillment.",
                )
            ),
            inline=False,
        )
    elif section == "help":
        budget.add_field(
            name="\u2728 Operator flow",
            value=(
                "Use Setup for routes, timezone, roles, eligibility, and delivery safety.\n"
                "Use Studio for celebration copy, Quiet vs Party style, previews, media tools, "
                "annual-event polish, capsules, quests, and surprises."
            ),
            inline=False,
        )
        for group_name, placeholders in supported_placeholder_groups():
            budget.add_line_fields(
                group_name,
                [f"`{{{name}}}` - {description}" for name, description in placeholders],
                inline=False,
            )
        budget.add_field(
            name="\U0001F517 Media URL examples",
            value=(
                "`https://cdn.example.com/birthday/banner.gif`\n"
                "`https://images.example.com/render?id=42&sig=abc123`\n"
                "`https://media.example.com/assets/celebration`\n"
                "`https://www.example.com/gallery/photo-42` is a webpage, not direct media."
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F389 Anniversary placeholder rules",
            value=(
                "`{anniversary.years}` - Valid on: Member anniversary only.\n"
                "`{server_anniversary.years_since_creation}` - Valid on: Server anniversary only.\n"
                "`{event.name}` / `{event.date}` / `{event.kind}` - Valid on: Member "
                "anniversary, Server anniversary, Recurring annual event."
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F9ED Preview and reset notes",
            value=(
                "Media Tools leads with the live route, media source, and health state.\n"
                "It validates image and thumbnail URLs before save.\n"
                "Signed, query-string, and extensionless URLs can work when validation proves "
                "they are direct media assets.\n"
                "Full preview is still the final Discord render check. It never pings members.\n"
                "Reset copy restores the default template. Surface reset clears the current "
                "route, image, and thumbnail overrides together, then inheritance applies only "
                "where those fields are unset. Global look covers theme, title, footer, accent, "
                "and global celebration behavior."
            ),
            inline=False,
        )
        budget.add_field(
            name="Safety limits",
            value=(
                "Studio blocks obvious profanity, NSFW wording, slurs, harassment-style text, "
                "and unsafe media URLs. It does not do image-content moderation."
            ),
            inline=False,
        )
    elif section == "birthday":
        _add_delivery_section(
            budget,
            settings=settings,
            template=settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE,
            routing_lines=(
                f"Live status: {_format_enabled(settings.announcements_enabled)}",
                _surface_route_lines(birthday_surface)[0],
                _surface_route_lines(birthday_surface)[1],
                f"Theme: {announcement_theme_label(settings.announcement_theme)}",
                "Global celebration behavior: "
                f"{_celebration_mode_label(settings.celebration_mode)}",
            ),
            field_label="\U0001F382 Birthday announcement copy",
            surface=birthday_surface,
        )
    elif section == "birthday_dm":
        budget.add_field(
            name="\U0001F48C Birthday DM copy",
            value=code_block_snippet(settings.birthday_dm_template or DEFAULT_DM_TEMPLATE),
            inline=False,
        )
        budget.add_line_fields(
            "Routing and behavior",
            (
                f"Live status: {_format_enabled(settings.birthday_dm_enabled)}",
                "Delivery model: best effort private DM",
                "Previews stay private and never ping anyone.",
            ),
            inline=False,
        )
        budget.add_line_fields(
            "\U0001F3A8 Theme coverage",
            _birthday_dm_presentation_lines(settings),
            inline=False,
        )
    elif section == "anniversary":
        _add_delivery_section(
            budget,
            settings=settings,
            template=settings.anniversary_template or DEFAULT_ANNIVERSARY_TEMPLATE,
            routing_lines=(
                f"Live status: {_format_enabled(settings.anniversary_enabled)}",
                _surface_route_lines(anniversary_surface)[0],
                _surface_route_lines(anniversary_surface)[1],
                "Model: tracked members only",
            ),
            field_label="\U0001F389 Member anniversary copy",
            surface=anniversary_surface,
        )
    elif section == "server_anniversary":
        budget.add_line_fields(
            "\U0001F3F0 Live behavior",
            (
                f"Live: {_format_enabled(state.enabled)}",
                f"Date: {_format_month_day(state.month, state.day)}",
                "Date source: "
                f"{'Guild creation date' if state.use_guild_created_date else 'Custom date'}",
                *_surface_route_lines(server_surface),
            ),
            inline=False,
        )
        budget.add_field(
            name="\U0001F4DD Server anniversary copy",
            value=code_block_snippet(
                state.template or default_template_for_kind("server_anniversary")
            ),
            inline=False,
        )
        budget.add_line_fields(
            "\U0001F3A8 Style and media",
            _presentation_lines(settings, surface=server_surface),
            inline=False,
        )
    elif section == "capsules":
        budget.add_line_fields(
            "Capsule controls",
            (
                f"Enabled: {_format_enabled(experience_state.capsules_enabled)}",
                "One queued wish per author-to-target pair",
                "Public reveal uses the birthday announcement route when available",
                (
                    "If no live route exists, unlocked wishes stay private to timeline and "
                    "admin preview"
                ),
            ),
            inline=False,
        )
        budget.add_field(
            name="Capsule moderation",
            value=(
                "Members can add, list, and remove their own queued wishes.\n"
                "Admins can preview queued capsules privately before reveal."
            ),
            inline=False,
        )
    elif section == "quests":
        budget.add_line_fields(
            "Quest controls",
            (
                f"Enabled: {_format_enabled(experience_state.quests_enabled)}",
                f"Wish target: {experience_state.quest_wish_target}",
                f"Reaction target: {experience_state.quest_reaction_target}",
                "Check-in required: "
                f"{_format_enabled(experience_state.quest_checkin_enabled)}",
                "Tracked objectives: unlocked wishes, shared-post reactions, and optional check-in",
            ),
            inline=False,
        )
        budget.add_field(
            name="Quest rewards",
            value=(
                "Completed quests can unlock a timeline badge and featured birthday marker.\n"
                "Reaction goals use the shared birthday announcement post when one exists. "
                "No message-content tracking is used."
            ),
            inline=False,
        )
    elif section == "surprises":
        budget.add_line_fields(
            "Surprise controls",
            (
                f"Enabled: {_format_enabled(experience_state.surprises_enabled)}",
                "Selection model: one weighted Birthday Surprise per celebration",
                "Nitro is manual-only and tracked as concierge fulfillment",
            ),
            inline=False,
        )
        budget.add_line_fields(
            "Configured rewards",
            _surprise_reward_lines(surprise_rewards),
            inline=False,
        )
    else:
        budget.add_line_fields(
            "\U0001F4C5 Live defaults",
            (
                *_surface_route_lines(recurring_surface),
                *_surface_media_lines(recurring_surface)[:3],
            ),
            inline=False,
        )
        if recurring_events:
            budget.add_line_fields(
                "\U0001F4C5 Configured events",
                [_format_event_line(celebration) for celebration in recurring_events],
                inline=False,
            )
        else:
            budget.add_field(
                name="\U0001F4C5 Configured events",
                value="No custom annual events are configured yet.",
                inline=False,
            )
        budget.add_field(
            name="\U0001F4CB Managing events",
            value=(
                "Use `/birthday event add`, `/birthday event edit`, and `/birthday event list` "
                "to manage the yearly calendar.\n"
                "Use `/birthday test-message surface:recurring_event` with an event id to dry-run "
                "the exact live render."
            ),
            inline=False,
        )

    budget.set_footer(
        "Use the section menu to move between birthday posts, DMs, anniversaries, "
        "capsules, quests, surprises, and yearly events."
    )
    return budget.build()

def _add_delivery_section(
    budget: BudgetedEmbed,
    *,
    settings: GuildSettings,
    template: str,
    routing_lines: tuple[str, ...],
    field_label: str,
    surface: ResolvedAnnouncementSurface,
) -> None:
    budget.add_field(name=field_label, value=code_block_snippet(template), inline=False)
    budget.add_line_fields("Live behavior", routing_lines, inline=False)
    budget.add_line_fields(
        "\U0001F3A8 Style and media",
        _presentation_lines(settings, surface=surface),
        inline=False,
    )

def _presentation_lines(
    settings: GuildSettings,
    *,
    surface: ResolvedAnnouncementSurface,
) -> tuple[str, ...]:
    return (
        *_style_summary_lines(settings),
        *_surface_media_lines(surface)[:3],
    )


def _media_validation_line(
    value: str | None,
    *,
    label: str,
    probe: MediaProbeResult | None = None,
) -> str:
    if value is None or not value.strip():
        return f"Info: {label}: Not provided"
    if probe is not None:
        display_url = strip_validated_direct_media_marker(probe.url)
        return (
            f"{_media_status_icon(probe.classification)} {label}: {probe.status_label()} | "
            f"{truncate_text(display_url or '', 72)} | {_short_media_summary(probe.summary, label)}"
        )
    assessment = assess_media_url(value, label=label)
    if assessment is None:
        return f"Info: {label}: Not provided"
    display_url = strip_validated_direct_media_marker(assessment.normalized_url)
    return (
        f"{_media_status_icon(assessment.classification)} {label}: {assessment.status_label()} | "
        f"{truncate_text(display_url or '', 72)} | "
        f"{_short_media_summary(assessment.summary, label)}"
    )


def _media_status_icon(classification: str) -> str:
    return {
        "direct_media": "✅",
        "webpage": "⚠️",
        "invalid_or_unsafe": "⛔",
        "unsupported_media": "⛔",
        "needs_validation": "🔎",
        "validation_unavailable": "⚠️",
    }.get(classification, "i")


def _short_media_summary(summary: str, label: str) -> str:
    prefix = f"{label} URL "
    if summary.startswith(prefix):
        return summary[len(prefix) :]
    return summary


def _validated_media_storage_value(
    value: str | None,
    probe: MediaProbeResult | None,
) -> str | None:
    if value is None or probe is None:
        return None
    assessment = assess_media_url(
        value,
        label=probe.label,
        allow_validated_marker=False,
    )
    if assessment is None:
        return None
    if assessment.classification == "needs_validation":
        return mark_validated_direct_media_url(probe.url)
    return probe.url


def _birthday_dm_presentation_lines(settings: GuildSettings) -> tuple[str, ...]:
    return (
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        f"Theme note: {announcement_theme_description(settings.announcement_theme)}",
        f"Style: {_celebration_mode_label(settings.celebration_mode)}",
        "Global look controls stay on public announcement surfaces. Birthday DMs reuse the "
        "theme and celebration style only.",
    )


def _enabled_reward_count(rewards: tuple[GuildSurpriseReward, ...]) -> int:
    return sum(1 for reward in rewards if reward.enabled and reward.weight > 0)


def _surprise_reward_lines(rewards: tuple[GuildSurpriseReward, ...]) -> tuple[str, ...]:
    if not rewards:
        return ("No Birthday Surprise rewards are configured yet.",)
    lines = []
    for reward in rewards:
        status = "live" if reward.enabled and reward.weight > 0 else "off"
        detail = f"{reward.label} - {status} - weight {reward.weight}"
        if reward.note_text:
            detail = f"{detail}\nNote: {reward.note_text}"
        lines.append(detail)
    return tuple(lines)


def _server_anniversary_state(
    *,
    guild: discord.Guild | None,
    celebration: RecurringCelebration | None,
) -> ServerAnniversaryState:
    if celebration is not None:
        return ServerAnniversaryState(
            enabled=celebration.enabled,
            name=celebration.name,
            month=celebration.event_month,
            day=celebration.event_day,
            channel_id=celebration.channel_id,
            template=celebration.template,
            use_guild_created_date=celebration.use_guild_created_date,
            exists=True,
        )
    created_at = guild.created_at if guild is not None else None
    return ServerAnniversaryState(
        enabled=False,
        name="Server anniversary",
        month=created_at.month if created_at is not None else None,
        day=created_at.day if created_at is not None else None,
        channel_id=None,
        template=None,
        use_guild_created_date=True,
        exists=False,
    )


def _section_description(section: SectionName) -> str:
    descriptions: dict[SectionName, str] = {
        "home": (
            "Everything that shapes live celebrations lives here: copy, global look, routing "
            "awareness, and operator previews."
        ),
        "birthday": (
            "This is the public birthday post: the main server announcement plus shared "
            "visual styling."
        ),
        "birthday_dm": (
            "This is the private birthday DM. It has its own copy, but still follows the "
            "saved theme."
        ),
        "anniversary": (
            "Tracked join anniversaries reuse the global look, but keep separate copy and routing."
        ),
        "server_anniversary": (
            "Treat the server birthday as a first-class annual celebration with explicit "
            "date, routing, and preview controls."
        ),
        "capsules": (
            "Birthday Capsules keep pre-written wishes private until the birthday unlocks."
        ),
        "quests": (
            "Birthday Quests stay compact: wish goals plus optional check-in, nothing noisier."
        ),
        "surprises": (
            "Birthday Surprises stay operator-trustworthy with weighted rewards and manual Nitro."
        ),
        "events": (
            "Custom annual events stay intentionally lightweight: one date, one optional "
            "channel, one yearly celebration."
        ),
        "help": (
            "Reference placeholders, media examples, preview expectations, and reset "
            "behavior before editing anything."
        ),
    }
    return descriptions[section]


def _format_channel(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id is not None else "Not set"


def _format_role(role_id: int | None) -> str:
    return f"<@&{role_id}>" if role_id is not None else "Not set"


def _format_eligibility_role(role_id: int | None) -> str:
    return _format_role(role_id) if role_id is not None else "Everyone"


def _format_enabled(value: bool) -> str:
    return "Enabled" if value else "Disabled"


def _format_accent_color(value: int | None) -> str:
    if value is None:
        return "Preset"
    return f"#{value:06X}"


def _format_month_day(month: int | None, day: int | None) -> str:
    if month is None or day is None:
        return "Not available yet"
    return f"{month_name[month]} {day}"


def _format_event_line(celebration: RecurringCelebration) -> str:
    return (
        f"`{celebration.id}` {celebration.name} | {month_name[celebration.event_month]} "
        f"{celebration.event_day} | {_format_enabled(celebration.enabled)} | "
        f"{_format_channel(celebration.channel_id)}"
    )

def build_server_anniversary_control_embed(
    settings: GuildSettings,
    *,
    announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None = None,
    guild: discord.Guild,
    celebration: RecurringCelebration | None,
    note: str | None = None,
) -> discord.Embed:
    state = _server_anniversary_state(guild=guild, celebration=celebration)
    resolved_surface = _resolve_surface(
        settings,
        announcement_surfaces,
        "server_anniversary",
        event_channel_id=state.channel_id,
    )
    budget = BudgetedEmbed.create(
        title="\U0001F3F0 Server Anniversary Controls",
        description=(
            "Choose live status, date source, and channel routing with native controls."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="\u2728 Update", value=note, inline=False)
    budget.add_line_fields(
        "Status and date",
        (
            f"Live status: {_format_enabled(state.enabled)}",
            f"Date: {_format_month_day(state.month, state.day)}",
            "Date source: "
            f"{'Guild creation date' if state.use_guild_created_date else 'Custom saved date'}",
        ),
        inline=False,
    )
    budget.add_line_fields(
        "Live delivery",
        (
            *_surface_route_lines(resolved_surface),
            *_surface_media_lines(resolved_surface)[:3],
        ),
        inline=False,
    )
    budget.add_field(
        name="Preview and copy",
        value=(
            "Use Preview below to see the exact current render.\n"
            "Server anniversary copy still lives in the main Studio section.\n"
            "Set custom date now opens month/day selects instead of a raw text form."
        ),
        inline=False,
    )
    budget.add_line_fields(
        "\U0001F3A8 Style and media",
        _presentation_lines(settings, surface=resolved_surface),
        inline=False,
    )
    budget.set_footer("Use the controls below, then return to Celebration Studio.")
    return budget.build()


def build_server_anniversary_date_picker_embed(
    *,
    guild: discord.Guild,
    celebration: RecurringCelebration | None,
    selected_month: int | None,
    selected_day: int | None,
    note: str | None = None,
) -> discord.Embed:
    state = _server_anniversary_state(guild=guild, celebration=celebration)
    budget = BudgetedEmbed.create(
        title="\U0001F4C6 Custom server anniversary date",
        description="Pick the saved month and day with selects instead of raw text input.",
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="Updated", value=note, inline=False)
    budget.add_line_fields(
        "Current selection",
        (
            f"Selected date: {_format_month_day(selected_month, selected_day)}",
            (
                f"Saved date: {_format_month_day(state.month, state.day)}"
                if not state.use_guild_created_date
                else "Saved date: using guild creation date"
            ),
            "Saving here switches the server anniversary to a custom saved date.",
        ),
        inline=False,
    )
    budget.set_footer("Choose a month and day, then save the custom date.")
    return budget.build()

class SetupView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        announcement_surfaces: (
            dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None
        ) = None,
        owner_id: int,
        guild: discord.Guild | None = None,
        birthday_service: BirthdayService | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.settings = settings
        self.announcement_surfaces = _normalized_surfaces(settings, announcement_surfaces)
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.add_item(AnnouncementChannelSelect(self))
        self.add_item(BirthdayRoleSelect(self))
        self.add_item(EligibilityRoleSelect(self))
        self.toggle_announcements.label = (
            "Disable announcements" if settings.announcements_enabled else "Enable announcements"
        )
        self.toggle_role_assignment.label = (
            "Disable birthday role" if settings.role_enabled else "Enable birthday role"
        )
        self.toggle_birthday_dm.label = (
            "Disable birthday DM" if settings.birthday_dm_enabled else "Enable birthday DM"
        )
        self.toggle_anniversary.label = (
            "Disable anniversaries" if settings.anniversary_enabled else "Enable anniversaries"
        )
        self.remove_item(self.toggle_ignore_bots)
        self.remove_item(self.refresh_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This setup panel belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, *, note: str | None = None) -> None:
        assert interaction.guild is not None
        latest = await self.settings_service.get_settings(interaction.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(
            interaction.guild.id
        )
        await interaction.response.edit_message(
            embed=build_setup_embed(latest, latest_surfaces, note),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
            ),
        )

    @discord.ui.button(label="Set timezone", style=discord.ButtonStyle.primary, row=3)
    async def set_timezone(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        await interaction.response.send_modal(
            TimezoneModal(
                settings_service=self.settings_service,
                current_timezone=self.settings.default_timezone,
                owner_id=self.owner_id,
                birthday_service=self.birthday_service,
            )
        )

    @discord.ui.button(label="Toggle announcements", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_announcements(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                announcements_enabled=not self.settings.announcements_enabled,
            )
        except ContentPolicyError as exc:
            await _audit_blocked_attempt(interaction, surface="studio_template", error=exc)
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.refresh(interaction, note="Birthday announcements updated.")

    @discord.ui.button(label="Toggle role", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_role_assignment(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                role_enabled=not self.settings.role_enabled,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.refresh(interaction, note="Birthday role setting updated.")

    @discord.ui.button(label="Toggle birthday DM", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_birthday_dm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        await self.settings_service.update_settings(
            interaction.guild,
            birthday_dm_enabled=not self.settings.birthday_dm_enabled,
        )
        await self.refresh(interaction, note="Birthday DM setting updated.")

    @discord.ui.button(label="Toggle anniversaries", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_anniversary(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                anniversary_enabled=not self.settings.anniversary_enabled,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.refresh(interaction, note="Anniversary setting updated.")

    @discord.ui.button(label="Toggle ignore bots", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_ignore_bots(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        await self.settings_service.update_settings(
            interaction.guild,
            ignore_bots=not self.settings.ignore_bots,
        )
        await self.refresh(interaction, note="Ignore-bots rule updated.")

    @discord.ui.button(label="Membership rules", style=discord.ButtonStyle.secondary, row=4)
    async def membership_rules(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_membership_rules_embed(latest),
            view=MembershipRulesView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Studio safety", style=discord.ButtonStyle.secondary, row=4)
    async def studio_safety(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_studio_safety_embed(latest),
            view=StudioSafetyView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Celebration Studio", style=discord.ButtonStyle.secondary, row=4)
    async def open_message_setup(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        latest = await self.settings_service.get_settings(interaction.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(
            interaction.guild.id
        )
        experience_service, experience_settings, surprise_rewards = await _load_experience_context(
            interaction.client,
            interaction.guild.id,
        )
        server_anniversary, recurring_events = await _load_studio_context(
            interaction.guild,
            self.birthday_service,
        )
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                section="home",
                guild=interaction.guild,
                experience_settings=experience_settings,
                surprise_rewards=surprise_rewards,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                experience_service=experience_service,
                experience_settings=experience_settings,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
                section="home",
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=4)
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        await self.refresh(interaction, note="Setup reloaded.")


class AnnouncementChannelSelect(discord.ui.ChannelSelect["SetupView"]):
    def __init__(self, setup_view: SetupView) -> None:
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Select birthday announcement channel",
            min_values=0,
            max_values=1,
            row=0,
        )
        self.setup_view = setup_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        channel_id = self.values[0].id if self.values else None
        try:
            await self.setup_view.settings_service.update_announcement_surface(
                interaction.guild,
                surface_kind="birthday_announcement",
                channel_id=channel_id,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.setup_view.refresh(interaction, note="Announcement channel updated.")


class BirthdayRoleSelect(discord.ui.RoleSelect["SetupView"]):
    def __init__(self, setup_view: SetupView) -> None:
        super().__init__(
            placeholder="Select dedicated birthday role",
            min_values=0,
            max_values=1,
            row=1,
        )
        self.setup_view = setup_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        role_id = self.values[0].id if self.values else None
        try:
            await self.setup_view.settings_service.update_settings(
                interaction.guild,
                birthday_role_id=role_id,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.setup_view.refresh(interaction, note="Birthday role updated.")


class EligibilityRoleSelect(discord.ui.RoleSelect["SetupView"]):
    def __init__(self, setup_view: SetupView) -> None:
        super().__init__(
            placeholder="Select optional eligibility role",
            min_values=0,
            max_values=1,
            row=2,
        )
        self.setup_view = setup_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        role_id = self.values[0].id if self.values else None
        try:
            await self.setup_view.settings_service.update_settings(
                interaction.guild,
                eligibility_role_id=role_id,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.setup_view.refresh(interaction, note="Eligibility rule updated.")


class MessageTemplateView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        experience_service: ExperienceService | None = None,
        experience_settings: GuildExperienceSettings | None = None,
        settings: GuildSettings,
        announcement_surfaces: (
            dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None
        ) = None,
        owner_id: int,
        guild: discord.Guild | None = None,
        birthday_service: BirthdayService | None = None,
        section: SectionName = "home",
        preview_kind: AnnouncementKind | None = None,
        server_anniversary: RecurringCelebration | None = None,
        recurring_events: tuple[RecurringCelebration, ...] = (),
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.experience_service = experience_service
        self.experience_settings = experience_settings or GuildExperienceSettings.default(
            settings.guild_id
        )
        self.settings = settings
        self.announcement_surfaces = _normalized_surfaces(settings, announcement_surfaces)
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.section = section
        self.preview_kind = preview_kind or _default_preview_kind_for_section(section)
        self.server_anniversary = server_anniversary
        self.recurring_events = recurring_events
        self.add_item(StudioSectionSelect(self))
        self.add_item(AnnouncementThemeSelect(self))
        self._configure_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This Celebration Studio panel belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def _latest_context(
        self,
        guild: discord.Guild,
    ) -> tuple[
        GuildSettings,
        dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings],
        GuildExperienceSettings,
        tuple[GuildSurpriseReward, ...],
        RecurringCelebration | None,
        tuple[RecurringCelebration, ...],
    ]:
        latest = await self.settings_service.get_settings(guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(guild.id)
        experience_settings = (
            await self.experience_service.get_settings(guild.id)
            if self.experience_service is not None
            else GuildExperienceSettings.default(guild.id)
        )
        surprise_rewards = (
            tuple(await self.experience_service.list_surprise_rewards(guild.id))
            if self.experience_service is not None
            else ()
        )
        server_anniversary, recurring_events = await _load_studio_context(
            guild,
            self.birthday_service,
        )
        return (
            latest,
            latest_surfaces,
            experience_settings,
            surprise_rewards,
            server_anniversary,
            recurring_events,
        )

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        section: SectionName | None = None,
        preview_kind: AnnouncementKind | None = None,
        note: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        (
            latest,
            latest_surfaces,
            experience_settings,
            surprise_rewards,
            server_anniversary,
            recurring_events,
        ) = await self._latest_context(
            interaction.guild
        )
        next_section = section or self.section
        next_preview_kind = (
            preview_kind
            or self.preview_kind
            if next_section == self.section
            else _default_preview_kind_for_section(next_section)
        )
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                note=note,
                section=next_section,
                guild=interaction.guild,
                experience_settings=experience_settings,
                surprise_rewards=surprise_rewards,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                experience_service=self.experience_service,
                experience_settings=experience_settings,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
                section=next_section,
                preview_kind=next_preview_kind,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
        )

    def _configure_buttons(self) -> None:
        if self.section == "birthday":
            self.edit_primary.label = "Edit birthday copy"
            self.edit_secondary.label = "Edit global look"
            self.preview_current.label = "Preview selected surface"
            self.reset_current.label = "Reset birthday copy"
        elif self.section == "birthday_dm":
            self.edit_primary.label = "Edit DM copy"
            self.edit_secondary.label = "Edit global look"
            self.preview_current.label = "Preview selected surface"
            self.reset_current.label = "Reset DM copy"
            self.reset_media.disabled = True
        elif self.section == "anniversary":
            self.edit_primary.label = "Edit anniversary copy"
            self.edit_secondary.label = "Edit global look"
            self.preview_current.label = "Preview selected surface"
            self.reset_current.label = "Reset anniversary copy"
        elif self.section == "server_anniversary":
            self.edit_primary.label = "Schedule controls"
            self.edit_secondary.label = "Edit event copy"
            self.preview_current.label = "Preview selected surface"
            self.reset_current.label = "Reset to guild date"
        elif self.section == "capsules":
            self.edit_primary.label = (
                "Disable capsules"
                if self.experience_settings.capsules_enabled
                else "Enable capsules"
            )
            self.edit_secondary.disabled = True
            self.preview_current.label = "Preview capsule rules"
            self.reset_current.disabled = True
            self.reset_media.disabled = True
        elif self.section == "quests":
            self.edit_primary.label = "Quest controls"
            self.edit_secondary.disabled = True
            self.preview_current.label = "Preview quest rules"
            self.reset_current.disabled = False
            self.reset_current.label = (
                "Disable quests"
                if self.experience_settings.quests_enabled
                else "Enable quests"
            )
            self.reset_current.style = discord.ButtonStyle.secondary
            self.reset_media.disabled = True
        elif self.section == "surprises":
            self.edit_primary.label = "Weight mix"
            self.edit_secondary.label = "Reward labels"
            self.preview_current.label = "Preview reward pool"
            self.reset_current.disabled = False
            self.reset_current.label = (
                "Disable surprises"
                if self.experience_settings.surprises_enabled
                else "Enable surprises"
            )
            self.reset_current.style = discord.ButtonStyle.secondary
            self.reset_media.disabled = True
        elif self.section == "events":
            self.edit_primary.label = "Event commands"
            self.edit_secondary.label = "Edit global look"
            self.preview_current.label = "Preview selected surface"
            self.reset_current.label = "Reset global look"
        elif self.section == "help":
            self.edit_primary.disabled = True
            self.edit_secondary.disabled = True
            self.preview_current.disabled = True
            self.reset_current.disabled = True
            self.reset_media.disabled = True
            self.toggle_celebration_behavior.disabled = True
        else:
            self.edit_primary.label = "Open birthday copy"
            self.edit_secondary.label = "Edit global look"
            self.preview_current.label = "Preview selected surface"
            self.reset_current.label = "Reset global look"
        if not self.reset_media.disabled:
            self.reset_media.label = _surface_media_button_label(self.section)
        self.toggle_celebration_behavior.label = (
            f"Style: {self.settings.celebration_mode.title()}"
        )

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=2)
    async def edit_primary(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        (
            latest,
            latest_surfaces,
            _experience_settings,
            _surprise_rewards,
            server_anniversary,
            recurring_events,
        ) = await self._latest_context(interaction.guild)
        if self.section == "server_anniversary":
            await interaction.response.send_message(
                embed=build_server_anniversary_control_embed(
                    latest,
                    announcement_surfaces=latest_surfaces,
                    guild=interaction.guild,
                    celebration=server_anniversary,
                ),
                view=ServerAnniversaryControlView(
                    settings_service=self.settings_service,
                    birthday_service=self.birthday_service,
                    settings=latest,
                    owner_id=self.owner_id,
                    guild=interaction.guild,
                    celebration=server_anniversary,
                    recurring_events=recurring_events,
                ),
                ephemeral=True,
            )
            return
        if self.section == "events":
            await interaction.response.send_message(
                "Manage custom annual events with `/birthday event add`, "
                "`/birthday event edit`, and `/birthday event list`.\n"
                "Use `/birthday test-message` with `surface: recurring_event` and an event id "
                "to dry-run one.",
                ephemeral=True,
            )
            return
        if self.section == "capsules":
            if self.experience_service is None:
                raise ValidationError("Experience settings are not available in this panel.")
            experience_settings = await self.experience_service.get_settings(interaction.guild.id)
            await self.experience_service.update_settings(
                interaction.guild.id,
                capsules_enabled=not experience_settings.capsules_enabled,
            )
            await self.refresh(
                interaction,
                note=(
                    "Birthday Capsules enabled."
                    if not experience_settings.capsules_enabled
                    else "Birthday Capsules disabled."
                ),
            )
            return
        if self.section == "quests":
            if self.experience_service is None:
                raise ValidationError("Experience settings are not available in this panel.")
            experience_settings = await self.experience_service.get_settings(interaction.guild.id)
            await interaction.response.send_message(
                embed=build_quest_settings_embed(experience_settings),
                view=QuestSettingsView(
                    experience_service=self.experience_service,
                    settings=experience_settings,
                    owner_id=self.owner_id,
                    guild=interaction.guild,
                ),
                ephemeral=True,
            )
            return
        if self.section == "surprises":
            if self.experience_service is None:
                raise ValidationError("Experience settings are not available in this panel.")
            rewards = tuple(
                await self.experience_service.list_surprise_rewards(interaction.guild.id)
            )
            await interaction.response.send_modal(
                SurpriseWeightsModal(
                    experience_service=self.experience_service,
                    rewards=rewards,
                    owner_id=self.owner_id,
                    birthday_service=self.birthday_service,
                )
            )
            return
        target: Literal["birthday", "birthday_dm", "anniversary", "server_anniversary"] = (
            "birthday"
            if self.section in {"home", "birthday"}
            else "birthday_dm"
            if self.section == "birthday_dm"
            else "anniversary"
        )
        await interaction.response.send_modal(
            TemplateEditModal(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                target=target,
                guild=interaction.guild,
                celebration=server_anniversary,
            )
        )

    @discord.ui.button(label="Edit visuals", style=discord.ButtonStyle.secondary, row=2)
    async def edit_secondary(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        (
            latest,
            latest_surfaces,
            _experience_settings,
            _surprise_rewards,
            server_anniversary,
            _recurring_events,
        ) = await self._latest_context(interaction.guild)
        if self.section == "surprises":
            if self.experience_service is None:
                raise ValidationError("Experience settings are not available in this panel.")
            rewards = tuple(
                await self.experience_service.list_surprise_rewards(interaction.guild.id)
            )
            await interaction.response.send_modal(
                SurpriseLabelsModal(
                    experience_service=self.experience_service,
                    rewards=rewards,
                    owner_id=self.owner_id,
                    birthday_service=self.birthday_service,
                )
            )
            return
        if self.section == "server_anniversary":
            await interaction.response.send_modal(
                TemplateEditModal(
                    settings_service=self.settings_service,
                    birthday_service=self.birthday_service,
                    settings=latest,
                    owner_id=self.owner_id,
                    target="server_anniversary",
                    guild=interaction.guild,
                    celebration=server_anniversary,
                )
            )
            return
        await interaction.response.send_modal(
            StudioPresentationModal(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                birthday_service=self.birthday_service,
                section=self.section,
            )
        )

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, row=2)
    async def preview_current(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        (
            latest,
            latest_surfaces,
            _experience_settings,
            _surprise_rewards,
            server_anniversary,
            recurring_events,
        ) = await self._latest_context(interaction.guild)
        if self.section in {"capsules", "quests", "surprises"}:
            await interaction.response.send_message(
                embed=build_message_template_embed(
                    latest,
                    announcement_surfaces=latest_surfaces,
                    section=self.section,
                    guild=interaction.guild,
                    experience_settings=_experience_settings,
                    surprise_rewards=_surprise_rewards,
                    server_anniversary=server_anniversary,
                    recurring_events=recurring_events,
                ),
                ephemeral=True,
            )
            return
        try:
            status_embed, preview_embed = await _build_studio_preview_pair(
                guild=interaction.guild,
                settings=latest,
                settings_service=self.settings_service,
                section=self.section,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
                announcement_surfaces=latest_surfaces,
                preview_kind=self.preview_kind,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            embeds=[status_embed, preview_embed],
            view=StudioPreviewView(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
                section=self.section,
                preview_kind=self.preview_kind,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=2)
    async def reset_current(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        (
            latest,
            _latest_surfaces,
            _experience_settings,
            _surprise_rewards,
            server_anniversary,
            recurring_events,
        ) = await self._latest_context(interaction.guild)
        if self.section == "quests":
            if self.experience_service is None:
                raise ValidationError("Experience settings are not available in this panel.")
            await self.experience_service.update_settings(
                interaction.guild.id,
                quests_enabled=not _experience_settings.quests_enabled,
            )
            await self.refresh(
                interaction,
                note=(
                    "Birthday Quests enabled."
                    if not _experience_settings.quests_enabled
                    else "Birthday Quests disabled."
                ),
            )
            return
        if self.section == "surprises":
            if self.experience_service is None:
                raise ValidationError("Experience settings are not available in this panel.")
            await self.experience_service.update_settings(
                interaction.guild.id,
                surprises_enabled=not _experience_settings.surprises_enabled,
            )
            await self.refresh(
                interaction,
                note=(
                    "Birthday Surprises enabled."
                    if not _experience_settings.surprises_enabled
                    else "Birthday Surprises disabled."
                ),
            )
            return
        try:
            note = await self._reset_section(
                interaction.guild,
                server_anniversary=server_anniversary,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        (
            latest,
            _latest_surfaces,
            _experience_settings,
            _surprise_rewards,
            server_anniversary,
            recurring_events,
        ) = await self._latest_context(interaction.guild)
        await interaction.response.send_message(
            embed=_build_return_embed("Celebration Studio updated", note),
            view=StudioReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                section=self.section,
                preview_kind=self.preview_kind,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Media tools", style=discord.ButtonStyle.secondary, row=3)
    async def reset_media(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        if _SECTION_SURFACE_KIND[self.section] is None:
            await interaction.response.send_message(
                "This section does not have its own route or media settings.",
                ephemeral=True,
            )
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(
            interaction.guild.id
        )
        await interaction.response.send_message(
            embed=build_media_tools_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                surface_kind=_SECTION_SURFACE_KIND[self.section] or "birthday_announcement",
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=interaction.guild,
                section=self.section,
                preview_kind=self.preview_kind,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.secondary, row=3)
    async def back_to_setup(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        assert interaction.guild is not None
        latest = await self.settings_service.get_settings(interaction.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(
            interaction.guild.id
        )
        await interaction.response.edit_message(
            embed=build_setup_embed(latest, latest_surfaces),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
            ),
        )

    @discord.ui.button(label="Behavior", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_celebration_behavior(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        assert interaction.guild is not None
        target_mode: Literal["quiet", "party"] = (
            "party" if self.settings.celebration_mode == "quiet" else "quiet"
        )
        await self.settings_service.update_settings(
            interaction.guild,
            celebration_mode=target_mode,
        )
        await self.refresh(
            interaction,
            note=f"Global celebration behavior saved as {target_mode.title()}.",
        )

    async def _reset_section(
        self,
        guild: discord.Guild,
        *,
        server_anniversary: RecurringCelebration | None,
    ) -> str:
        if self.section == "birthday":
            await self.settings_service.update_settings(guild, announcement_template=None)
            return "Birthday announcement copy reset to the safe default."
        if self.section == "birthday_dm":
            await self.settings_service.update_settings(guild, birthday_dm_template=None)
            return "Birthday DM copy reset to the safe default."
        if self.section == "anniversary":
            await self.settings_service.update_settings(guild, anniversary_template=None)
            return "Member anniversary copy reset to the safe default."
        if self.section == "server_anniversary":
            if self.birthday_service is None:
                raise ValidationError("Server anniversary tools are not available in this panel.")
            await self.birthday_service.reset_server_anniversary(
                guild_id=guild.id,
                guild_created_at_utc=guild.created_at,
                enabled=server_anniversary.enabled if server_anniversary else False,
            )
            return "Server anniversary reset to the guild creation date."
        await self.settings_service.update_settings(
            guild,
            announcement_title_override=None,
            announcement_footer_text=None,
            announcement_accent_color=None,
        )
        return "Global look reset to the current theme preset."


class StudioSectionSelect(discord.ui.Select["MessageTemplateView"]):
    def __init__(self, message_view: MessageTemplateView) -> None:
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                description=_section_select_description(key),
                default=key == message_view.section,
            )
            for key, label in _SECTION_LABELS.items()
        ]
        super().__init__(
            placeholder=f"Section: {_SECTION_LABELS[message_view.section]}",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.message_view = message_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.message_view.refresh(
            interaction,
            section=self.values[0],  # type: ignore[arg-type]
        )


class AnnouncementThemeSelect(discord.ui.Select["MessageTemplateView"]):
    def __init__(self, message_view: MessageTemplateView) -> None:
        options = [
            discord.SelectOption(
                label=spec.label,
                value=spec.key,
                description=truncate_text(spec.description, 100),
                default=spec.key == message_view.settings.announcement_theme,
            )
            for spec in supported_announcement_themes()
        ]
        super().__init__(
            placeholder=(
                "Theme: "
                f"{announcement_theme_label(message_view.settings.announcement_theme)}"
            ),
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )
        self.message_view = message_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.message_view.settings_service.update_settings(
            interaction.guild,
            announcement_theme=self.values[0],  # type: ignore[arg-type]
        )
        await self.message_view.refresh(interaction, note="Announcement theme saved.")


class StudioPreviewSurfaceSelect(discord.ui.Select["StudioPreviewView"]):
    def __init__(self, preview_view: StudioPreviewView) -> None:
        options = [
            discord.SelectOption(
                label=surface_label(kind),
                value=kind,
                description={
                    "birthday_announcement": "Preview the public birthday announcement surface.",
                    "birthday_dm": "Preview the private birthday DM surface.",
                    "anniversary": "Preview the member-anniversary announcement surface.",
                    "server_anniversary": "Preview the server-anniversary announcement surface.",
                    "recurring_event": "Preview the recurring annual event announcement surface.",
                }[kind],
                default=kind == preview_view.preview_kind,
            )
            for kind in _PREVIEW_SURFACES
        ]
        super().__init__(
            placeholder=f"Preview surface: {surface_label(preview_view.preview_kind)}",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.preview_view = preview_view

    async def callback(self, interaction: discord.Interaction) -> None:
        preview_kind = cast(AnnouncementKind, self.values[0])
        await self.preview_view.refresh(
            interaction,
            preview_kind=preview_kind,
            note=f"Preview target set to {surface_label(preview_kind)}.",
        )


class StudioPreviewView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        announcement_surfaces: dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings],
        owner_id: int,
        guild: discord.Guild,
        birthday_service: BirthdayService | None,
        section: SectionName,
        preview_kind: AnnouncementKind,
        server_anniversary: RecurringCelebration | None,
        recurring_events: tuple[RecurringCelebration, ...],
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.settings = settings
        self.announcement_surfaces = announcement_surfaces
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.section = section
        self.preview_kind = preview_kind
        self.server_anniversary = server_anniversary
        self.recurring_events = recurring_events
        self.add_item(StudioPreviewSurfaceSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This preview belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        preview_kind: AnnouncementKind,
        note: str | None = None,
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        status_embed, preview_embed = await _build_studio_preview_pair(
            guild=self.guild,
            settings=latest,
            settings_service=self.settings_service,
            section=self.section,
            announcement_surfaces=latest_surfaces,
            preview_kind=preview_kind,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        )
        if note is not None:
            status_embed.insert_field_at(0, name="Updated", value=note, inline=False)
        await interaction.response.edit_message(
            embeds=[status_embed, preview_embed],
            view=StudioPreviewView(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section=self.section,
                preview_kind=preview_kind,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class StudioReturnView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
        section: SectionName,
        preview_kind: AnnouncementKind | None = None,
        server_anniversary: RecurringCelebration | None = None,
        recurring_events: tuple[RecurringCelebration, ...] = (),
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.section = section
        self.preview_kind = preview_kind or _default_preview_kind_for_section(section)
        self.server_anniversary = server_anniversary
        self.recurring_events = recurring_events

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This update belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Back to Celebration Studio", style=discord.ButtonStyle.primary)
    async def back(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioReturnView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        experience_service, experience_settings, surprise_rewards = await _load_experience_context(
            interaction.client,
            self.guild.id,
        )
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                section=self.section,
                guild=self.guild,
                experience_settings=experience_settings,
                surprise_rewards=surprise_rewards,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                experience_service=experience_service,
                experience_settings=experience_settings,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section=self.section,
                preview_kind=self.preview_kind,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
        )


class SetupReturnView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This update belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.primary)
    async def back(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupReturnView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        await interaction.response.edit_message(
            embed=build_setup_embed(latest, latest_surfaces),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
            ),
        )


class MembershipRulesView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
        birthday_service: BirthdayService | None,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.add_item(MembershipIgnoreBotsSelect(self))
        self.add_item(MembershipAgePresetSelect(self))
        self.add_item(MembershipMentionPresetSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "These membership controls belong to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, *, note: str | None = None) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        await interaction.response.edit_message(
            embed=build_membership_rules_embed(latest, note=note),
            view=MembershipRulesView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
            ),
        )

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.secondary, row=3)
    async def back_to_setup(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MembershipRulesView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        await interaction.response.edit_message(
            embed=_build_return_embed(
                "Membership rules",
                "Return to the main Setup panel.",
            ),
            view=SetupReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
            ),
        )


class MembershipIgnoreBotsSelect(discord.ui.Select["MembershipRulesView"]):
    def __init__(self, control_view: MembershipRulesView) -> None:
        super().__init__(
            placeholder=(
                "Ignore bot accounts: "
                f"{'On' if control_view.settings.ignore_bots else 'Off'}"
            ),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Ignore bot accounts",
                    value="on",
                    description="Skip bot members entirely during celebration delivery.",
                    default=control_view.settings.ignore_bots,
                ),
                discord.SelectOption(
                    label="Include bot accounts",
                    value="off",
                    description="Allow bot members to stay eligible if other rules pass.",
                    default=not control_view.settings.ignore_bots,
                ),
            ],
            row=0,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.control_view.settings_service.update_settings(
            interaction.guild,
            ignore_bots=self.values[0] == "on",
        )
        await self.control_view.refresh(interaction, note="Ignore-bots rule updated.")


class MembershipAgePresetSelect(discord.ui.Select["MembershipRulesView"]):
    def __init__(self, control_view: MembershipRulesView) -> None:
        current = control_view.settings.minimum_membership_days
        preset_values = (0, 1, 3, 7, 30, 90)
        super().__init__(
            placeholder=f"Minimum membership age: {current} day(s)",
            min_values=1,
            max_values=1,
            options=[
                *[
                    discord.SelectOption(
                        label=f"{value} day{'s' if value != 1 else ''}",
                        value=str(value),
                        default=current == value,
                    )
                    for value in preset_values
                ],
                discord.SelectOption(
                    label="Custom number",
                    value="custom",
                    description="Enter a specific minimum day count.",
                    default=current not in preset_values,
                ),
            ],
            row=1,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "custom":
            await interaction.response.send_modal(
                MembershipRuleNumberModal(
                    settings_service=self.control_view.settings_service,
                    owner_id=self.control_view.owner_id,
                    guild=self.control_view.guild,
                    birthday_service=self.control_view.birthday_service,
                    field_name="minimum_membership_days",
                    label="Minimum membership age",
                    current_value=self.control_view.settings.minimum_membership_days,
                    note="Minimum membership age updated.",
                )
            )
            return
        assert interaction.guild is not None
        await self.control_view.settings_service.update_settings(
            interaction.guild,
            minimum_membership_days=int(self.values[0]),
        )
        await self.control_view.refresh(interaction, note="Minimum membership age updated.")


class MembershipMentionPresetSelect(discord.ui.Select["MembershipRulesView"]):
    def __init__(self, control_view: MembershipRulesView) -> None:
        current = control_view.settings.mention_suppression_threshold
        preset_values = (1, 3, 5, 10, 15, 25, 50)
        super().__init__(
            placeholder=f"Mention suppression threshold: {current}",
            min_values=1,
            max_values=1,
            options=[
                *[
                    discord.SelectOption(
                        label=str(value),
                        value=str(value),
                        description="Suppress mentions when a live batch reaches this size.",
                        default=current == value,
                    )
                    for value in preset_values
                ],
                discord.SelectOption(
                    label="Custom number",
                    value="custom",
                    description="Enter a specific suppression threshold.",
                    default=current not in preset_values,
                ),
            ],
            row=2,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "custom":
            await interaction.response.send_modal(
                MembershipRuleNumberModal(
                    settings_service=self.control_view.settings_service,
                    owner_id=self.control_view.owner_id,
                    guild=self.control_view.guild,
                    birthday_service=self.control_view.birthday_service,
                    field_name="mention_suppression_threshold",
                    label="Mention suppression threshold",
                    current_value=self.control_view.settings.mention_suppression_threshold,
                    note="Mention suppression threshold updated.",
                )
            )
            return
        assert interaction.guild is not None
        await self.control_view.settings_service.update_settings(
            interaction.guild,
            mention_suppression_threshold=int(self.values[0]),
        )
        await self.control_view.refresh(interaction, note="Mention suppression threshold updated.")


class MembershipRuleNumberModal(AdminPanelModal):
    value_input: discord.ui.TextInput[MembershipRuleNumberModal] = discord.ui.TextInput(
        label="Value",
        required=True,
        max_length=4,
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        owner_id: int,
        guild: discord.Guild,
        birthday_service: BirthdayService | None,
        field_name: Literal["minimum_membership_days", "mention_suppression_threshold"],
        label: str,
        current_value: int,
        note: str,
    ) -> None:
        super().__init__(title=label)
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.field_name = field_name
        self.note = note
        self.value_input.label = label
        self.value_input.default = str(current_value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = int(self.value_input.value)
            if self.field_name == "minimum_membership_days":
                await self.settings_service.update_settings(
                    self.guild,
                    minimum_membership_days=value,
                )
            else:
                await self.settings_service.update_settings(
                    self.guild,
                    mention_suppression_threshold=value,
                )
        except (ValidationError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(self.guild.id)
        await interaction.response.send_message(
            embed=build_membership_rules_embed(latest, note=self.note),
            view=MembershipRulesView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
            ),
            ephemeral=True,
        )


class QuestSettingsView(AdminPanelView):
    def __init__(
        self,
        *,
        experience_service: ExperienceService,
        settings: GuildExperienceSettings,
        owner_id: int,
        guild: discord.Guild,
    ) -> None:
        super().__init__(timeout=600)
        self.experience_service = experience_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.add_item(QuestWishTargetSelect(self))
        self.add_item(QuestReactionTargetSelect(self))
        self.toggle_enabled.label = "Disable live" if settings.quests_enabled else "Enable live"
        self.toggle_checkin.label = (
            "Disable check-in" if settings.quest_checkin_enabled else "Enable check-in"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "These quest controls belong to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, *, note: str | None = None) -> None:
        latest = await self.experience_service.get_settings(self.guild.id)
        await interaction.response.edit_message(
            embed=build_quest_settings_embed(latest, note=note),
            view=QuestSettingsView(
                experience_service=self.experience_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
            ),
        )

    @discord.ui.button(label="Enable live", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_enabled(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QuestSettingsView],
    ) -> None:
        await self.experience_service.update_settings(
            self.guild.id,
            quests_enabled=not self.settings.quests_enabled,
        )
        await self.refresh(
            interaction,
            note=(
                "Birthday Quests enabled."
                if not self.settings.quests_enabled
                else "Birthday Quests disabled."
            ),
        )

    @discord.ui.button(label="Enable check-in", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_checkin(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QuestSettingsView],
    ) -> None:
        await self.experience_service.update_settings(
            self.guild.id,
            quest_checkin_enabled=not self.settings.quest_checkin_enabled,
        )
        await self.refresh(
            interaction,
            note=(
                "Quest check-in enabled."
                if not self.settings.quest_checkin_enabled
                else "Quest check-in disabled."
            ),
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.primary, row=3)
    async def done(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QuestSettingsView],
    ) -> None:
        await interaction.response.edit_message(
            embed=_build_return_embed(
                "Quest controls",
                "Return to the main Celebration Studio panel.",
            ),
            view=None,
        )


class QuestWishTargetSelect(discord.ui.Select["QuestSettingsView"]):
    def __init__(self, control_view: QuestSettingsView) -> None:
        current = control_view.settings.quest_wish_target
        options = (1, 2, 3, 5, 7, 10, 15, 20, 25)
        super().__init__(
            placeholder=f"Wish target: {current}",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=str(value),
                    value=str(value),
                    default=current == value,
                )
                for value in options
            ],
            row=0,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.control_view.experience_service.update_settings(
            self.control_view.guild.id,
            quest_wish_target=int(self.values[0]),
        )
        await self.control_view.refresh(interaction, note="Quest wish target updated.")


class QuestReactionTargetSelect(discord.ui.Select["QuestSettingsView"]):
    def __init__(self, control_view: QuestSettingsView) -> None:
        current = control_view.settings.quest_reaction_target
        options = (1, 2, 3, 5, 7, 10, 15, 20, 25)
        super().__init__(
            placeholder=f"Reaction target: {current}",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=str(value),
                    value=str(value),
                    default=current == value,
                )
                for value in options
            ],
            row=1,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.control_view.experience_service.update_settings(
            self.control_view.guild.id,
            quest_reaction_target=int(self.values[0]),
        )
        await self.control_view.refresh(interaction, note="Quest reaction target updated.")


class StudioSafetyChannelSelect(discord.ui.ChannelSelect["StudioSafetyView"]):
    def __init__(self, safety_view: StudioSafetyView) -> None:
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Select optional Studio audit channel",
            min_values=0,
            max_values=1,
            row=0,
        )
        self.safety_view = safety_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        channel_id = self.values[0].id if self.values else None
        try:
            await self.safety_view.settings_service.update_settings(
                interaction.guild,
                studio_audit_channel_id=channel_id,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.safety_view.settings_service.get_settings(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_studio_safety_embed(
                latest,
                note=(
                    "Studio audit logging enabled."
                    if channel_id is not None
                    else "Studio audit logging disabled."
                ),
            ),
            view=StudioSafetyView(
                settings_service=self.safety_view.settings_service,
                birthday_service=self.safety_view.birthday_service,
                settings=latest,
                owner_id=self.safety_view.owner_id,
                guild=interaction.guild,
            ),
        )


class StudioSafetyView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.add_item(StudioSafetyChannelSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This Studio safety panel belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.secondary, row=1)
    async def back_to_setup(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioSafetyView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        await interaction.response.edit_message(
            embed=build_setup_embed(latest, latest_surfaces),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
            ),
        )


class TimezoneModal(AdminPanelModal, title="Set default timezone"):
    timezone: discord.ui.TextInput[TimezoneModal] = discord.ui.TextInput(
        label="IANA timezone",
        placeholder="Asia/Yerevan or Europe/Berlin",
        required=True,
        max_length=64,
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        current_timezone: str,
        owner_id: int,
        birthday_service: BirthdayService | None,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        self.timezone.default = current_timezone

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                default_timezone=self.timezone.value,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=_build_return_embed(
                "Timezone saved",
                f"Default timezone saved as `{latest.default_timezone}`.",
            ),
            view=SetupReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )


class SurpriseWeightsModal(AdminPanelModal, title="Birthday Surprise weights"):
    featured_weight: discord.ui.TextInput[SurpriseWeightsModal] = discord.ui.TextInput(
        label="Featured weight",
        required=True,
        max_length=4,
    )
    badge_weight: discord.ui.TextInput[SurpriseWeightsModal] = discord.ui.TextInput(
        label="Badge weight",
        required=True,
        max_length=4,
    )
    custom_note_weight: discord.ui.TextInput[SurpriseWeightsModal] = discord.ui.TextInput(
        label="Custom note weight",
        required=True,
        max_length=4,
    )
    nitro_weight: discord.ui.TextInput[SurpriseWeightsModal] = discord.ui.TextInput(
        label="Nitro concierge weight",
        required=True,
        max_length=4,
    )

    def __init__(
        self,
        *,
        experience_service: ExperienceService,
        rewards: tuple[GuildSurpriseReward, ...],
        owner_id: int,
        birthday_service: BirthdayService | None,
    ) -> None:
        super().__init__()
        self.experience_service = experience_service
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        reward_map = {reward.reward_type: reward for reward in rewards}
        self.featured_weight.default = str(reward_map["featured"].weight)
        self.badge_weight.default = str(reward_map["badge"].weight)
        self.custom_note_weight.default = str(reward_map["custom_note"].weight)
        self.nitro_weight.default = str(reward_map["nitro_concierge"].weight)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        rewards = await self.experience_service.list_surprise_rewards(interaction.guild.id)
        reward_map = {reward.reward_type: reward for reward in rewards}
        await self.experience_service.update_surprise_rewards(
            interaction.guild.id,
            updates={
                "featured": {
                    "enabled": reward_map["featured"].enabled,
                    "weight": int(self.featured_weight.value),
                },
                "badge": {
                    "enabled": reward_map["badge"].enabled,
                    "weight": int(self.badge_weight.value),
                },
                "custom_note": {
                    "enabled": reward_map["custom_note"].enabled,
                    "weight": int(self.custom_note_weight.value),
                },
                "nitro_concierge": {
                    "enabled": reward_map["nitro_concierge"].enabled,
                    "weight": int(self.nitro_weight.value),
                },
            },
        )
        await interaction.response.send_message(
            embed=_build_return_embed(
                "Birthday Surprises updated",
                "Surprise weights were updated.",
            ),
            ephemeral=True,
        )


class SurpriseLabelsModal(AdminPanelModal, title="Birthday Surprise labels"):
    featured_label: discord.ui.TextInput[SurpriseLabelsModal] = discord.ui.TextInput(
        label="Featured label",
        required=True,
        max_length=80,
    )
    badge_label: discord.ui.TextInput[SurpriseLabelsModal] = discord.ui.TextInput(
        label="Badge label",
        required=True,
        max_length=80,
    )
    custom_note_label: discord.ui.TextInput[SurpriseLabelsModal] = discord.ui.TextInput(
        label="Custom reward label",
        required=True,
        max_length=80,
    )
    custom_note_text: discord.ui.TextInput[SurpriseLabelsModal] = discord.ui.TextInput(
        label="Custom reward note",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=200,
    )
    nitro_label: discord.ui.TextInput[SurpriseLabelsModal] = discord.ui.TextInput(
        label="Nitro concierge label",
        required=True,
        max_length=80,
    )

    def __init__(
        self,
        *,
        experience_service: ExperienceService,
        rewards: tuple[GuildSurpriseReward, ...],
        owner_id: int,
        birthday_service: BirthdayService | None,
    ) -> None:
        super().__init__()
        self.experience_service = experience_service
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        reward_map = {reward.reward_type: reward for reward in rewards}
        self.featured_label.default = reward_map["featured"].label
        self.badge_label.default = reward_map["badge"].label
        self.custom_note_label.default = reward_map["custom_note"].label
        self.custom_note_text.default = reward_map["custom_note"].note_text or ""
        self.nitro_label.default = reward_map["nitro_concierge"].label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        rewards = await self.experience_service.list_surprise_rewards(interaction.guild.id)
        reward_map = {reward.reward_type: reward for reward in rewards}
        await self.experience_service.update_surprise_rewards(
            interaction.guild.id,
            updates={
                "featured": {
                    "enabled": reward_map["featured"].enabled,
                    "weight": reward_map["featured"].weight,
                    "label": self.featured_label.value,
                },
                "badge": {
                    "enabled": reward_map["badge"].enabled,
                    "weight": reward_map["badge"].weight,
                    "label": self.badge_label.value,
                },
                "custom_note": {
                    "enabled": reward_map["custom_note"].enabled,
                    "weight": reward_map["custom_note"].weight,
                    "label": self.custom_note_label.value,
                    "note_text": self.custom_note_text.value or None,
                },
                "nitro_concierge": {
                    "enabled": reward_map["nitro_concierge"].enabled,
                    "weight": reward_map["nitro_concierge"].weight,
                    "label": self.nitro_label.value,
                },
            },
        )
        await interaction.response.send_message(
            embed=_build_return_embed(
                "Birthday Surprise labels updated",
                "Reward labels and the custom manual reward note were updated.",
            ),
            ephemeral=True,
        )


class TemplateEditModal(AdminPanelModal):
    template_input: discord.ui.TextInput[TemplateEditModal] = discord.ui.TextInput(
        label="Template",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1200,
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        target: Literal["birthday", "birthday_dm", "anniversary", "server_anniversary"],
        guild: discord.Guild | None,
        celebration: RecurringCelebration | None,
    ) -> None:
        super().__init__(title=f"Edit {target.replace('_', ' ')} template")
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.owner_id = owner_id
        self.target = target
        self.guild = guild
        self.celebration = celebration
        if target == "birthday":
            self.template_input.default = (
                settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE
            )
        elif target == "birthday_dm":
            self.template_input.default = settings.birthday_dm_template or DEFAULT_DM_TEMPLATE
        elif target == "anniversary":
            self.template_input.default = (
                settings.anniversary_template or DEFAULT_ANNIVERSARY_TEMPLATE
            )
        else:
            self.template_input.default = (
                celebration.template
                if celebration
                else default_template_for_kind("server_anniversary")
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        value = self.template_input.value.strip() or None
        try:
            if self.target == "birthday":
                await self.settings_service.update_settings(
                    interaction.guild,
                    announcement_template=value,
                )
                section: SectionName = "birthday"
                note = "Birthday announcement copy saved."
            elif self.target == "birthday_dm":
                await self.settings_service.update_settings(
                    interaction.guild,
                    birthday_dm_template=value,
                )
                section = "birthday_dm"
                note = "Birthday DM copy saved."
            elif self.target == "anniversary":
                await self.settings_service.update_settings(
                    interaction.guild,
                    anniversary_template=value,
                )
                section = "anniversary"
                note = "Member anniversary copy saved."
            else:
                if self.birthday_service is None:
                    raise ValidationError(
                        "Server anniversary tools are not available in this panel."
                    )
                existing = self.celebration
                await self.birthday_service.upsert_server_anniversary(
                    guild_id=interaction.guild.id,
                    guild_created_at_utc=interaction.guild.created_at,
                    override_month=(
                        None
                        if existing and existing.use_guild_created_date
                        else existing.event_month if existing else None
                    ),
                    override_day=(
                        None
                        if existing and existing.use_guild_created_date
                        else existing.event_day if existing else None
                    ),
                    channel_id=existing.channel_id if existing else None,
                    template=value,
                    enabled=existing.enabled if existing else False,
                    use_guild_created_date=existing.use_guild_created_date if existing else True,
                )
                section = "server_anniversary"
                note = "Server anniversary copy saved."
        except ContentPolicyError as exc:
            await _audit_blocked_attempt(interaction, surface="studio_template", error=exc)
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            interaction.guild,
            self.birthday_service,
        )
        await interaction.response.send_message(
            embed=_build_return_embed("Celebration Studio updated", note),
            view=StudioReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                section=section,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            ephemeral=True,
        )


class StudioPresentationModal(AdminPanelModal, title="Edit global look"):
    title_override: discord.ui.TextInput[StudioPresentationModal] = discord.ui.TextInput(
        label="Title override",
        required=False,
        max_length=256,
    )
    footer_text: discord.ui.TextInput[StudioPresentationModal] = discord.ui.TextInput(
        label="Footer text",
        required=False,
        max_length=512,
    )
    accent_color: discord.ui.TextInput[StudioPresentationModal] = discord.ui.TextInput(
        label="Accent color",
        required=False,
        max_length=7,
        placeholder="#FFB347",
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
        birthday_service: BirthdayService | None,
        section: SectionName,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        self.section = section
        self.title_override.default = settings.announcement_title_override or ""
        self.footer_text.default = settings.announcement_footer_text or ""
        self.accent_color.default = (
            f"#{settings.announcement_accent_color:06X}"
            if settings.announcement_accent_color is not None
            else ""
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                announcement_title_override=self.title_override.value,
                announcement_footer_text=self.footer_text.value,
                announcement_accent_color=self.accent_color.value,
            )
        except ContentPolicyError as exc:
            await _audit_blocked_attempt(interaction, surface="studio_presentation", error=exc)
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            interaction.guild,
            self.birthday_service,
        )
        await interaction.response.send_message(
            embed=_build_return_embed(
                "Global look saved",
                "Global title, footer, and accent color were updated.",
            ),
            view=StudioReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                section=self.section,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            ephemeral=True,
        )


class MediaEditModal(AdminPanelModal, title="Update surface media"):
    image_url: discord.ui.TextInput[MediaEditModal] = discord.ui.TextInput(
        label="Image or GIF URL",
        required=False,
        max_length=500,
    )
    thumbnail_url: discord.ui.TextInput[MediaEditModal] = discord.ui.TextInput(
        label="Thumbnail URL",
        required=False,
        max_length=500,
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        announcement_surfaces: (
            dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None
        ) = None,
        owner_id: int,
        birthday_service: BirthdayService | None,
        guild: discord.Guild,
        section: SectionName,
        preview_kind: AnnouncementKind | None = None,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        self.guild = guild
        self.section = section
        self.preview_kind = preview_kind or _default_preview_kind_for_section(section)
        self.surface_kind = _SECTION_SURFACE_KIND[section] or "birthday_announcement"
        current_surface = _normalized_surfaces(settings, announcement_surfaces)[self.surface_kind]
        self.image_url.default = strip_validated_direct_media_marker(
            current_surface.image_url
        ) or ""
        self.thumbnail_url.default = strip_validated_direct_media_marker(
            current_surface.thumbnail_url
        ) or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        image_value = self.image_url.value.strip() or None
        thumbnail_value = self.thumbnail_url.value.strip() or None
        surface_title = surface_label(self.surface_kind)
        image_result, thumbnail_result = await asyncio.gather(
            probe_media_url(image_value, label=f"{surface_title} image"),
            probe_media_url(thumbnail_value, label=f"{surface_title} thumbnail"),
        )
        unsafe_results = tuple(
            result
            for result in (image_result, thumbnail_result)
            if result is not None and result.classification == "invalid_or_unsafe"
        )
        if unsafe_results:
            await _audit_blocked_media_attempt(
                interaction,
                surface="studio_media",
                field_labels=tuple(result.label for result in unsafe_results),
            )
        if any(
            result is not None and result.classification != "direct_media"
            for result in (image_result, thumbnail_result)
        ):
            latest = await self.settings_service.get_settings(interaction.guild.id)
            latest_surfaces = await self.settings_service.get_announcement_surfaces(
                interaction.guild.id
            )
            await interaction.response.send_message(
                embed=build_media_tools_embed(
                    latest,
                    announcement_surfaces=latest_surfaces,
                    surface_kind=self.surface_kind,
                    note="No changes were saved. Your current saved media is unchanged.",
                    image_probe=image_result,
                    thumbnail_probe=thumbnail_result,
                    checked_image_url=image_value,
                    checked_thumbnail_url=thumbnail_value,
                ),
                view=StudioMediaView(
                    settings_service=self.settings_service,
                    birthday_service=self.birthday_service,
                    settings=latest,
                    announcement_surfaces=latest_surfaces,
                    owner_id=self.owner_id,
                    guild=self.guild,
                    section=self.section,
                    preview_kind=self.preview_kind,
                ),
                ephemeral=True,
            )
            return
        saved_image_value = _validated_media_storage_value(image_value, image_result)
        saved_thumbnail_value = _validated_media_storage_value(
            thumbnail_value,
            thumbnail_result,
        )
        await self.settings_service.update_validated_media(
            interaction.guild,
            surface_kind=self.surface_kind,
            announcement_image_url=saved_image_value,
            announcement_thumbnail_url=saved_thumbnail_value,
        )
        latest = await self.settings_service.get_settings(interaction.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(
            interaction.guild.id
        )
        await interaction.response.send_message(
            embed=build_media_tools_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                surface_kind=self.surface_kind,
                note=f"{surface_title} media saved.",
                image_probe=image_result,
                thumbnail_probe=thumbnail_result,
                checked_image_url=image_value,
                checked_thumbnail_url=thumbnail_value,
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                section=self.section,
                preview_kind=self.preview_kind,
            ),
            ephemeral=True,
        )


class SurfaceRouteSelect(discord.ui.ChannelSelect["StudioMediaView"]):
    def __init__(self, media_view: StudioMediaView) -> None:
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder=f"Default route for {surface_label(media_view.surface_kind)}",
            min_values=0,
            max_values=1,
            row=1,
        )
        self.media_view = media_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        channel_id = self.values[0].id if self.values else None
        try:
            await self.media_view.settings_service.update_announcement_surface(
                interaction.guild,
                surface_kind=self.media_view.surface_kind,
                channel_id=channel_id,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        note = (
            f"{surface_label(self.media_view.surface_kind)} default route saved."
            if channel_id is not None
            else (
                f"{surface_label(self.media_view.surface_kind)} route override cleared."
                if self.media_view.surface_kind == "birthday_announcement"
                else f"{surface_label(self.media_view.surface_kind)} now inherits its route."
            )
        )
        await self.media_view.refresh(interaction, note=note)


class StudioMediaView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        announcement_surfaces: (
            dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None
        ) = None,
        owner_id: int,
        guild: discord.Guild,
        section: SectionName,
        preview_kind: AnnouncementKind | None = None,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.announcement_surfaces = _normalized_surfaces(settings, announcement_surfaces)
        self.owner_id = owner_id
        self.guild = guild
        self.section = section
        self.preview_kind = preview_kind or _default_preview_kind_for_section(section)
        self.surface_kind = _SECTION_SURFACE_KIND[section] or "birthday_announcement"
        self.add_item(SurfaceRouteSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "These media tools belong to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        note: str | None = None,
        image_probe: MediaProbeResult | None = None,
        thumbnail_probe: MediaProbeResult | None = None,
        checked_image_url: str | None = None,
        checked_thumbnail_url: str | None = None,
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        await interaction.response.edit_message(
            embed=build_media_tools_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                surface_kind=self.surface_kind,
                note=note,
                image_probe=image_probe,
                thumbnail_probe=thumbnail_probe,
                checked_image_url=checked_image_url,
                checked_thumbnail_url=checked_thumbnail_url,
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                section=self.section,
                preview_kind=self.preview_kind,
            ),
        )

    @discord.ui.button(label="Edit media", style=discord.ButtonStyle.primary, row=0)
    async def edit_media(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        await interaction.response.send_modal(
            MediaEditModal(
                settings_service=self.settings_service,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                birthday_service=self.birthday_service,
                guild=self.guild,
                section=self.section,
                preview_kind=self.preview_kind,
            )
        )

    @discord.ui.button(label="Validate current", style=discord.ButtonStyle.secondary, row=0)
    async def validate_current(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        resolved_surface = _resolve_surface(latest, latest_surfaces, self.surface_kind)
        checked_image_url = strip_validated_direct_media_marker(
            resolved_surface.image.effective_value
        )
        checked_thumbnail_url = strip_validated_direct_media_marker(
            resolved_surface.thumbnail.effective_value
        )
        image_result, thumbnail_result = await asyncio.gather(
            probe_media_url(
                checked_image_url,
                label=f"{surface_label(self.surface_kind)} image",
            ),
            probe_media_url(
                checked_thumbnail_url,
                label=f"{surface_label(self.surface_kind)} thumbnail",
            ),
        )
        await self.refresh(
            interaction,
            note=f"Effective {surface_label(self.surface_kind)} media was validated.",
            image_probe=image_result,
            thumbnail_probe=thumbnail_result,
            checked_image_url=checked_image_url,
            checked_thumbnail_url=checked_thumbnail_url,
        )

    @discord.ui.button(label="Clear route", style=discord.ButtonStyle.secondary, row=2)
    async def clear_route(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        await self.settings_service.update_announcement_surface(
            self.guild,
            surface_kind=self.surface_kind,
            channel_id=None,
        )
        note = (
            f"{surface_label(self.surface_kind)} route cleared."
            if self.surface_kind == "birthday_announcement"
            else (
                f"{surface_label(self.surface_kind)} route now inherits "
                "from Birthday announcement."
            )
        )
        await self.refresh(interaction, note=note)

    @discord.ui.button(label="Clear image", style=discord.ButtonStyle.secondary, row=2)
    async def clear_image(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        await self.settings_service.update_validated_media(
            self.guild,
            surface_kind=self.surface_kind,
            announcement_image_url=None,
        )
        await self.refresh(
            interaction,
            note=(
                f"{surface_label(self.surface_kind)} image cleared."
                if self.surface_kind == "birthday_announcement"
                else (
                    f"{surface_label(self.surface_kind)} image override cleared. "
                    "Image now inherits from Birthday announcement when available."
                )
            ),
        )

    @discord.ui.button(label="Clear thumbnail", style=discord.ButtonStyle.secondary, row=2)
    async def clear_thumbnail(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        await self.settings_service.update_validated_media(
            self.guild,
            surface_kind=self.surface_kind,
            announcement_thumbnail_url=None,
        )
        await self.refresh(
            interaction,
            note=(
                f"{surface_label(self.surface_kind)} thumbnail cleared."
                if self.surface_kind == "birthday_announcement"
                else (
                    f"{surface_label(self.surface_kind)} thumbnail override cleared. "
                    "Thumbnail now inherits from Birthday announcement when available."
                )
            ),
        )

    @discord.ui.button(label="Reset to inherited", style=discord.ButtonStyle.danger, row=3)
    async def reset_surface(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        await self.settings_service.update_announcement_surface(
            self.guild,
            surface_kind=self.surface_kind,
            channel_id=None,
            image_url=None,
            thumbnail_url=None,
        )
        note = (
            "Birthday announcement route and media cleared."
            if self.surface_kind == "birthday_announcement"
            else (
                f"{surface_label(self.surface_kind)} route, image, and thumbnail now inherit "
                "from Birthday announcement where available."
            )
        )
        await self.refresh(interaction, note=note)

    @discord.ui.button(label="Back to studio", style=discord.ButtonStyle.secondary, row=3)
    async def back_to_studio(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        experience_service, experience_settings, surprise_rewards = await _load_experience_context(
            interaction.client,
            self.guild.id,
        )
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                section=self.section,
                guild=self.guild,
                experience_settings=experience_settings,
                surprise_rewards=surprise_rewards,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                experience_service=experience_service,
                experience_settings=experience_settings,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section=self.section,
                preview_kind=self.preview_kind,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
        )


class ServerAnniversaryControlView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
        celebration: RecurringCelebration | None,
        recurring_events: tuple[RecurringCelebration, ...],
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.celebration = celebration
        self.recurring_events = recurring_events
        self.add_item(ServerAnniversaryChannelSelect(self))
        self.add_item(ServerAnniversaryDateSourceSelect(self))
        current_state = _server_anniversary_state(guild=guild, celebration=celebration)
        self.toggle_enabled.label = "Disable live" if current_state.enabled else "Enable live"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "These server-anniversary controls belong to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def _latest_context(
        self,
    ) -> tuple[
        GuildSettings,
        dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings],
        RecurringCelebration | None,
        tuple[RecurringCelebration, ...],
    ]:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        return latest, latest_surfaces, server_anniversary, recurring_events

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        note: str | None = None,
    ) -> None:
        latest, latest_surfaces, server_anniversary, recurring_events = await self._latest_context()
        await interaction.response.edit_message(
            embed=build_server_anniversary_control_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                guild=self.guild,
                celebration=server_anniversary,
                note=note,
            ),
            view=ServerAnniversaryControlView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                celebration=server_anniversary,
                recurring_events=recurring_events,
            ),
        )

    async def _save(
        self,
        *,
        enabled: bool | None = None,
        use_guild_created_date: bool | None = None,
        channel_id: int | None | object = _UI_UNSET,
        override_month: int | None | object = _UI_UNSET,
        override_day: int | None | object = _UI_UNSET,
    ) -> None:
        if self.birthday_service is None:
            raise ValidationError("Server anniversary tools are not available in this panel.")
        current = await self.birthday_service.get_server_anniversary(self.guild.id)
        state = _server_anniversary_state(guild=self.guild, celebration=current)
        target_enabled = state.enabled if enabled is None else enabled
        target_use_guild_created_date = (
            state.use_guild_created_date
            if use_guild_created_date is None
            else use_guild_created_date
        )
        target_channel_id = state.channel_id if channel_id is _UI_UNSET else channel_id
        target_month = state.month if override_month is _UI_UNSET else override_month
        target_day = state.day if override_day is _UI_UNSET else override_day
        assert target_channel_id is None or isinstance(target_channel_id, int)
        assert target_month is None or isinstance(target_month, int)
        assert target_day is None or isinstance(target_day, int)
        await self.birthday_service.upsert_server_anniversary(
            guild_id=self.guild.id,
            guild_created_at_utc=self.guild.created_at,
            override_month=(
                None
                if target_use_guild_created_date
                else int(target_month) if target_month is not None else None
            ),
            override_day=(
                None
                if target_use_guild_created_date
                else int(target_day) if target_day is not None else None
            ),
            channel_id=target_channel_id,
            template=current.template if current is not None else None,
            enabled=target_enabled,
            use_guild_created_date=target_use_guild_created_date,
        )

    @discord.ui.button(label="Enable live", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_enabled(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryControlView],
    ) -> None:
        current = (
            await self.birthday_service.get_server_anniversary(self.guild.id)
            if self.birthday_service is not None
            else None
        )
        current_state = _server_anniversary_state(guild=self.guild, celebration=current)
        await self._save(enabled=not current_state.enabled)
        await self.refresh(
            interaction,
            note=(
                "Server anniversary delivery was enabled."
                if not current_state.enabled
                else "Server anniversary delivery was disabled."
            ),
        )

    @discord.ui.button(label="Set custom date", style=discord.ButtonStyle.primary, row=2)
    async def set_custom_date(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryControlView],
    ) -> None:
        latest, _latest_surfaces, server_anniversary, recurring_events = (
            await self._latest_context()
        )
        state = _server_anniversary_state(guild=self.guild, celebration=server_anniversary)
        await interaction.response.edit_message(
            embed=build_server_anniversary_date_picker_embed(
                guild=self.guild,
                celebration=server_anniversary,
                selected_month=state.month,
                selected_day=state.day,
            ),
            view=ServerAnniversaryDatePickerView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                celebration=server_anniversary,
                recurring_events=recurring_events,
                selected_month=state.month,
                selected_day=state.day,
            ),
        )

    @discord.ui.button(label="Clear channel", style=discord.ButtonStyle.secondary, row=2)
    async def clear_channel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryControlView],
    ) -> None:
        await self._save(channel_id=None)
        await self.refresh(
            interaction,
            note="Server anniversary now uses the main birthday announcement channel.",
        )

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, row=3)
    async def preview(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryControlView],
    ) -> None:
        latest, latest_surfaces, server_anniversary, recurring_events = await self._latest_context()
        status_embed, preview_embed = await _build_studio_preview_pair(
            guild=self.guild,
            settings=latest,
            settings_service=self.settings_service,
            section="server_anniversary",
            announcement_surfaces=latest_surfaces,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        )
        await interaction.response.send_message(
            embeds=[status_embed, preview_embed],
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Reset to guild date", style=discord.ButtonStyle.danger, row=3)
    async def reset_to_guild_date(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryControlView],
    ) -> None:
        if self.birthday_service is None:
            await interaction.response.send_message(
                "Server anniversary tools are not available in this panel.",
                ephemeral=True,
            )
            return
        current = await self.birthday_service.get_server_anniversary(self.guild.id)
        current_state = _server_anniversary_state(guild=self.guild, celebration=current)
        await self.birthday_service.reset_server_anniversary(
            guild_id=self.guild.id,
            guild_created_at_utc=self.guild.created_at,
            enabled=current_state.enabled,
        )
        await self.refresh(interaction, note="Server anniversary reset to the guild creation date.")

    @discord.ui.button(label="Back to Studio", style=discord.ButtonStyle.secondary, row=3)
    async def back_to_studio(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryControlView],
    ) -> None:
        latest, latest_surfaces, server_anniversary, recurring_events = await self._latest_context()
        experience_service, experience_settings, surprise_rewards = await _load_experience_context(
            interaction.client,
            self.guild.id,
        )
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                section="server_anniversary",
                guild=self.guild,
                experience_settings=experience_settings,
                surprise_rewards=surprise_rewards,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                experience_service=experience_service,
                experience_settings=experience_settings,
                settings=latest,
                announcement_surfaces=latest_surfaces,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section="server_anniversary",
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
        )


class ServerAnniversaryDatePickerView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
        celebration: RecurringCelebration | None,
        recurring_events: tuple[RecurringCelebration, ...],
        selected_month: int | None,
        selected_day: int | None,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.celebration = celebration
        self.recurring_events = recurring_events
        self.selected_month = selected_month
        self.selected_day = selected_day
        self.add_item(ServerAnniversaryMonthSelect(self))
        self.add_item(ServerAnniversaryDaySelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "These server-anniversary controls belong to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    async def _latest_context(
        self,
    ) -> tuple[
        GuildSettings,
        dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings],
        RecurringCelebration | None,
        tuple[RecurringCelebration, ...],
    ]:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        return latest, latest_surfaces, server_anniversary, recurring_events

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        selected_month: int | None = None,
        selected_day: int | None = None,
        note: str | None = None,
    ) -> None:
        latest, _latest_surfaces, server_anniversary, recurring_events = (
            await self._latest_context()
        )
        effective_month = (
            selected_month if selected_month is not None else self.selected_month
        )
        effective_day = selected_day if selected_day is not None else self.selected_day
        await interaction.response.edit_message(
            embed=build_server_anniversary_date_picker_embed(
                guild=self.guild,
                celebration=server_anniversary,
                selected_month=effective_month,
                selected_day=effective_day,
                note=note,
            ),
            view=ServerAnniversaryDatePickerView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                celebration=server_anniversary,
                recurring_events=recurring_events,
                selected_month=effective_month,
                selected_day=effective_day,
            ),
        )

    @discord.ui.button(label="Save custom date", style=discord.ButtonStyle.primary, row=2)
    async def save(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryDatePickerView],
    ) -> None:
        if self.birthday_service is None:
            await interaction.response.send_message(
                "Server anniversary tools are not available in this panel.",
                ephemeral=True,
            )
            return
        try:
            existing = await self.birthday_service.get_server_anniversary(self.guild.id)
            await self.birthday_service.upsert_server_anniversary(
                guild_id=self.guild.id,
                guild_created_at_utc=self.guild.created_at,
                override_month=self.selected_month,
                override_day=self.selected_day,
                channel_id=existing.channel_id if existing is not None else None,
                template=existing.template if existing is not None else None,
                enabled=existing.enabled if existing is not None else False,
                use_guild_created_date=False,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=_build_return_embed(
                "Server anniversary updated",
                "Custom server-anniversary date saved.",
            ),
            view=ServerAnniversaryReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                owner_id=self.owner_id,
                guild=self.guild,
            ),
        )

    @discord.ui.button(label="Back to controls", style=discord.ButtonStyle.secondary, row=2)
    async def back_to_controls(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryDatePickerView],
    ) -> None:
        latest, latest_surfaces, server_anniversary, recurring_events = await self._latest_context()
        await interaction.response.edit_message(
            embed=build_server_anniversary_control_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                guild=self.guild,
                celebration=server_anniversary,
            ),
            view=ServerAnniversaryControlView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                celebration=server_anniversary,
                recurring_events=recurring_events,
            ),
        )


class ServerAnniversaryMonthSelect(discord.ui.Select["ServerAnniversaryDatePickerView"]):
    def __init__(self, control_view: ServerAnniversaryDatePickerView) -> None:
        super().__init__(
            placeholder=(
                f"Month: {month_name[control_view.selected_month]}"
                if control_view.selected_month is not None
                else "Select month"
            ),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=month_name[month],
                    value=str(month),
                    default=control_view.selected_month == month,
                )
                for month in range(1, 13)
            ],
            row=0,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        month = int(self.values[0])
        selected_day = self.control_view.selected_day
        if selected_day is not None:
            max_day = monthrange(2024 if month == 2 else 2025, month)[1]
            selected_day = min(selected_day, max_day)
        await self.control_view.refresh(
            interaction,
            selected_month=month,
            selected_day=selected_day,
        )


class ServerAnniversaryDaySelect(discord.ui.Select["ServerAnniversaryDatePickerView"]):
    def __init__(self, control_view: ServerAnniversaryDatePickerView) -> None:
        month = control_view.selected_month or 1
        max_day = monthrange(2024 if month == 2 else 2025, month)[1]
        super().__init__(
            placeholder=(
                f"Day: {control_view.selected_day}"
                if control_view.selected_day is not None
                else "Select day"
            ),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=str(day),
                    value=str(day),
                    default=control_view.selected_day == day,
                )
                for day in range(1, max_day + 1)
            ],
            row=1,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.control_view.refresh(
            interaction,
            selected_day=int(self.values[0]),
        )


class ServerAnniversaryChannelSelect(discord.ui.ChannelSelect["ServerAnniversaryControlView"]):
    def __init__(self, control_view: ServerAnniversaryControlView) -> None:
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Optional channel override for the server anniversary",
            min_values=0,
            max_values=1,
            row=0,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        channel_id = self.values[0].id if self.values else None
        await self.control_view._save(channel_id=channel_id)
        await self.control_view.refresh(
            interaction,
            note=(
                "Server anniversary channel override updated."
                if channel_id is not None
                else "Server anniversary now uses the main birthday announcement channel."
            ),
        )


class ServerAnniversaryDateSourceSelect(discord.ui.Select["ServerAnniversaryControlView"]):
    def __init__(self, control_view: ServerAnniversaryControlView) -> None:
        state = _server_anniversary_state(
            guild=control_view.guild,
            celebration=control_view.celebration,
        )
        super().__init__(
            placeholder=(
                "Date source: "
                f"{'Guild creation date' if state.use_guild_created_date else 'Custom saved date'}"
            ),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Guild creation date",
                    value="guild",
                    description="Use the server's created-at date when Discord provides it.",
                    default=state.use_guild_created_date,
                ),
                discord.SelectOption(
                    label="Custom saved date",
                    value="custom",
                    description="Use a custom month/day for the yearly server anniversary.",
                    default=not state.use_guild_created_date,
                ),
            ],
            row=1,
        )
        self.control_view = control_view

    async def callback(self, interaction: discord.Interaction) -> None:
        use_guild_created_date = self.values[0] == "guild"
        await self.control_view._save(use_guild_created_date=use_guild_created_date)
        await self.control_view.refresh(
            interaction,
            note=(
                "Server anniversary now follows the guild creation date."
                if use_guild_created_date
                else "Server anniversary now uses a custom saved date."
            ),
        )


class ServerAnniversaryReturnView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        owner_id: int,
        guild: discord.Guild,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.owner_id = owner_id
        self.guild = guild

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This update belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Back to server anniversary controls",
        style=discord.ButtonStyle.primary,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ServerAnniversaryReturnView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        latest_surfaces = await self.settings_service.get_announcement_surfaces(self.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        await interaction.response.edit_message(
            embed=build_server_anniversary_control_embed(
                latest,
                announcement_surfaces=latest_surfaces,
                guild=self.guild,
                celebration=server_anniversary,
            ),
            view=ServerAnniversaryControlView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                celebration=server_anniversary,
                recurring_events=recurring_events,
            ),
        )


async def _build_studio_preview_pair(
    *,
    guild: discord.Guild,
    settings: GuildSettings,
    settings_service: SettingsService,
    section: SectionName,
    server_anniversary: RecurringCelebration | None,
    recurring_events: tuple[RecurringCelebration, ...],
    announcement_surfaces: (
        dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings] | None
    ) = None,
    preview_kind: AnnouncementKind | None = None,
) -> tuple[discord.Embed, discord.Embed]:
    if section == "help":
        raise ValidationError("Select a delivery section before previewing.")
    selected_kind: AnnouncementKind = preview_kind or _default_preview_kind_for_section(section)
    resolved_surface: ResolvedAnnouncementSurface | None = None
    if selected_kind == "birthday_announcement":
        preview = preview_context_for_kind("birthday_announcement")
        resolved_surface = _resolve_surface(
            settings,
            announcement_surfaces,
            "birthday_announcement",
        )
        readiness = await settings_service.describe_delivery(
            guild,
            kind="birthday_announcement",
        )
        try:
            ensure_safe_announcement_inputs(
                template=settings.announcement_template,
                template_label="Birthday announcement template",
                title_override=settings.announcement_title_override,
                footer_text=settings.announcement_footer_text,
            )
            preview_embed = build_announcement_message(
                kind="birthday_announcement",
                server_name=guild.name,
                recipients=preview.recipients,
                celebration_mode=settings.celebration_mode,
                announcement_theme=settings.announcement_theme,
                presentation=resolved_surface.presentation(settings),
                template=settings.announcement_template,
                preview_label="Preview only - birthday announcement",
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    kind=selected_kind,
                    resolved_surface=resolved_surface,
                    mention_suppressed=(
                        len(preview.recipients) >= settings.mention_suppression_threshold
                    ),
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    elif selected_kind == "birthday_dm":
        preview = preview_context_for_kind("birthday_dm")
        readiness = await settings_service.describe_delivery(guild, kind="birthday_dm")
        try:
            ensure_safe_announcement_inputs(
                template=settings.birthday_dm_template,
                template_label="Birthday DM template",
                title_override=None,
                footer_text=None,
            )
            preview_embed = build_announcement_message(
                kind="birthday_dm",
                server_name=guild.name,
                recipients=preview.recipients,
                celebration_mode=settings.celebration_mode,
                announcement_theme=settings.announcement_theme,
                presentation=settings.presentation_for_kind("birthday_dm"),
                template=settings.birthday_dm_template,
                preview_label="Preview only - birthday DM",
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    kind=selected_kind,
                    resolved_surface=None,
                    mention_suppressed=False,
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    elif selected_kind == "anniversary":
        preview = preview_context_for_kind("anniversary")
        resolved_surface = _resolve_surface(
            settings,
            announcement_surfaces,
            "anniversary",
        )
        readiness = await settings_service.describe_delivery(guild, kind="anniversary")
        try:
            ensure_safe_announcement_inputs(
                template=settings.anniversary_template,
                template_label="Anniversary template",
                title_override=settings.announcement_title_override,
                footer_text=settings.announcement_footer_text,
                event_name=preview.event_name,
                event_name_label="Anniversary event name",
            )
            preview_embed = build_announcement_message(
                kind="anniversary",
                server_name=guild.name,
                recipients=preview.recipients,
                celebration_mode=settings.celebration_mode,
                announcement_theme=settings.announcement_theme,
                presentation=resolved_surface.presentation(settings),
                template=settings.anniversary_template,
                preview_label="Preview only - member anniversary",
                event_name=preview.event_name,
                event_month=preview.event_month,
                event_day=preview.event_day,
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    kind=selected_kind,
                    resolved_surface=resolved_surface,
                    mention_suppressed=(
                        len(preview.recipients) >= settings.mention_suppression_threshold
                    ),
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    elif selected_kind == "server_anniversary":
        state = _server_anniversary_state(guild=guild, celebration=server_anniversary)
        if state.month is None or state.day is None:
            raise ValidationError(
                "Discord did not provide the guild creation date. "
                "Save a custom server-anniversary date first."
            )
        resolved_surface = _resolve_surface(
            settings,
            announcement_surfaces,
            "server_anniversary",
            event_channel_id=state.channel_id,
        )
        readiness = await settings_service.describe_delivery(
            guild,
            kind="server_anniversary",
            channel_id=state.channel_id,
        )
        try:
            ensure_safe_announcement_inputs(
                template=state.template,
                template_label="Server anniversary template",
                title_override=settings.announcement_title_override,
                footer_text=settings.announcement_footer_text,
                event_name=state.name,
                event_name_label="Server anniversary name",
            )
            preview_embed = build_announcement_message(
                kind="server_anniversary",
                server_name=guild.name,
                recipients=[],
                celebration_mode=settings.celebration_mode,
                announcement_theme=settings.announcement_theme,
                presentation=resolved_surface.presentation(settings),
                template=state.template,
                preview_label="Preview only - server anniversary",
                event_name=state.name,
                event_month=state.month,
                event_day=state.day,
                server_anniversary_years_since_creation=(
                    server_anniversary_years_since_creation(
                        guild.created_at,
                        now_utc=datetime.now(UTC),
                    )
                    if guild.created_at is not None
                    else None
                ),
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    kind=selected_kind,
                    resolved_surface=resolved_surface,
                    mention_suppressed=False,
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    else:
        if not recurring_events:
            raise ValidationError("Create a recurring annual event before previewing one here.")
        celebration = recurring_events[0]
        resolved_surface = _resolve_surface(
            settings,
            announcement_surfaces,
            "recurring_event",
            event_channel_id=celebration.channel_id,
        )
        readiness = await settings_service.describe_delivery(
            guild,
            kind="recurring_event",
            channel_id=celebration.channel_id,
        )
        try:
            ensure_safe_announcement_inputs(
                template=celebration.template,
                template_label="Recurring event template",
                title_override=settings.announcement_title_override,
                footer_text=settings.announcement_footer_text,
                event_name=celebration.name,
            )
            preview_embed = build_announcement_message(
                kind="recurring_event",
                server_name=guild.name,
                recipients=[],
                celebration_mode=settings.celebration_mode,
                announcement_theme=settings.announcement_theme,
                presentation=resolved_surface.presentation(settings),
                template=celebration.template,
                preview_label=f"Preview only - {celebration.name}",
                event_name=celebration.name,
                event_month=celebration.event_month,
                event_day=celebration.event_day,
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    kind=selected_kind,
                    resolved_surface=resolved_surface,
                    mention_suppressed=False,
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    return (
        _build_preview_status_embed(
            settings,
            readiness,
            kind=selected_kind,
            resolved_surface=resolved_surface,
            mention_suppressed=(
                len(preview.recipients) >= settings.mention_suppression_threshold
                if selected_kind in {"birthday_announcement", "anniversary"}
                else False
            ),
        ),
        preview_embed,
    )


def _build_preview_status_embed(
    settings: GuildSettings,
    readiness: object,
    *,
    kind: AnnouncementKind,
    resolved_surface: ResolvedAnnouncementSurface | None,
    mention_suppressed: bool,
    preview_error: str | None = None,
) -> discord.Embed:
    from bdayblaze.domain.models import AnnouncementDeliveryReadiness

    assert isinstance(readiness, AnnouncementDeliveryReadiness)
    presentation = _preview_presentation_for_kind(
        settings,
        kind=kind,
        resolved_surface=resolved_surface,
    )
    media_diagnostics = build_presentation_diagnostics(presentation)
    budget = BudgetedEmbed.create(
        title="\U0001F9EA Dry-Run Preview",
        description="Preview only. No live celebration was sent.",
        color=discord.Color.green() if readiness.status == "ready" else discord.Color.orange(),
    )
    budget.add_field(name="Preview surface", value=surface_label(kind), inline=False)
    budget.add_field(name="Live delivery readiness", value=readiness.summary, inline=False)
    if readiness.details:
        budget.add_line_fields("Details", readiness.details, inline=False)
    route_lines = (
        ("Route: private DM only", "Route source: direct DM flow")
        if kind == "birthday_dm"
        else _surface_route_lines(
            resolved_surface
            if resolved_surface is not None
            else raise_preview_surface_error()
        )
    )
    budget.add_line_fields(
        "Routing and mentions",
        (
            *route_lines,
            _preview_mention_status(kind=kind, mention_suppressed=mention_suppressed),
        ),
        inline=False,
    )
    budget.add_line_fields(
        "Media and visuals",
        _preview_visual_lines(
            settings,
            kind=kind,
            resolved_surface=resolved_surface,
            media_diagnostics=media_diagnostics,
        ),
        inline=False,
    )
    if media_diagnostics:
        budget.add_line_fields(
            "Media diagnostics",
            [diagnostic.detail_line() for diagnostic in media_diagnostics],
            inline=False,
        )
    if preview_error:
        budget.add_field(name="Preview blocked", value=preview_error, inline=False)
    return budget.build()

def _build_preview_unavailable_embed(section_label: str, reason: str) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Preview unavailable",
        description=reason,
        color=discord.Color.orange(),
    )
    budget.add_field(
        name="What to do next",
        value=(
            f"Review `{section_label}`, fix the blocked setting, and rerun preview.\n"
            "Live delivery should not be treated as ready until this preview succeeds."
        ),
        inline=False,
    )
    return budget.build()


def _preview_mention_status(
    *,
    kind: AnnouncementKind,
    mention_suppressed: bool,
) -> str:
    if kind == "birthday_dm":
        return "Mentions: not used in private DMs."
    if kind in {"server_anniversary", "recurring_event"}:
        return "Mentions: not used for this celebration type."
    if mention_suppressed:
        return "Mentions: would be suppressed for a batch this size."
    return "Mentions: would be allowed for a small live batch."


def _preview_presentation_for_kind(
    settings: GuildSettings,
    *,
    kind: AnnouncementKind,
    resolved_surface: ResolvedAnnouncementSurface | None,
) -> AnnouncementStudioPresentation:
    if kind == "birthday_dm":
        return settings.presentation_for_kind(kind)
    if resolved_surface is None:
        raise ValidationError("Preview surface resolution is unavailable.")
    return resolved_surface.presentation(settings)


def _preview_visual_lines(
    settings: GuildSettings,
    *,
    kind: AnnouncementKind,
    resolved_surface: ResolvedAnnouncementSurface | None,
    media_diagnostics: tuple[object, ...],
) -> tuple[str, ...]:
    if kind == "birthday_dm":
        return (
            "Media status: Not used for live birthday DMs",
            f"Theme: {announcement_theme_label(settings.announcement_theme)}",
            "Global celebration behavior: "
            f"{_celebration_mode_label(settings.celebration_mode)}",
            "Global look controls stay on public announcement surfaces.",
        )
    if resolved_surface is None:
        raise ValidationError("Preview surface resolution is unavailable.")
    return (
        f"Media status: {'Ready' if not media_diagnostics else 'Needs attention'}",
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        "Global celebration behavior: "
        f"{_celebration_mode_label(settings.celebration_mode)}",
        f"Title override: {settings.announcement_title_override or 'Default'}",
        *_surface_media_lines(resolved_surface),
    )


def raise_preview_surface_error() -> ResolvedAnnouncementSurface:
    raise ValidationError("Preview surface resolution is unavailable.")


async def _load_studio_context(
    guild: discord.Guild,
    birthday_service: BirthdayService | None,
) -> tuple[RecurringCelebration | None, tuple[RecurringCelebration, ...]]:
    if birthday_service is None:
        return None, ()
    server_anniversary = await birthday_service.get_server_anniversary(guild.id)
    recurring_events = tuple(
        await birthday_service.list_recurring_celebrations(
            guild.id,
            limit=8,
        )
    )
    return server_anniversary, recurring_events


async def _load_experience_context(
    client: discord.Client,
    guild_id: int,
) -> tuple[ExperienceService | None, GuildExperienceSettings, tuple[GuildSurpriseReward, ...]]:
    container = getattr(client, "container", None)
    experience_service = getattr(container, "experience_service", None)
    if experience_service is None:
        return None, GuildExperienceSettings.default(guild_id), ()
    return (
        experience_service,
        await experience_service.get_settings(guild_id),
        tuple(await experience_service.list_surprise_rewards(guild_id)),
    )


def _build_return_embed(title: str, note: str) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title=title,
        description=note,
        color=discord.Color.blurple(),
    )
    budget.set_footer("Use the button below to return to the current admin panel.")
    return budget.build()


def _section_select_description(section: SectionName) -> str:
    descriptions: dict[SectionName, str] = {
        "home": "Overview of all celebration surfaces",
        "birthday": "Public birthday post copy and visuals",
        "birthday_dm": "Private birthday DM copy",
        "anniversary": "Tracked member anniversary setup",
        "server_anniversary": "Server birthday controls and copy",
        "capsules": "Birthday Capsule settings and moderation",
        "quests": "Birthday Quest rules and rewards",
        "surprises": "Birthday Surprise pool and Nitro concierge",
        "events": "Yearly custom event overview",
        "help": "Placeholders, media examples, and reset notes",
    }
    return descriptions[section]


def _parse_optional_int(value: str, *, label: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValidationError(f"{label} must be a number.") from exc


def _parse_yes_no(value: str, *, label: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"y", "yes", "true", "on"}:
        return True
    if normalized in {"n", "no", "false", "off"}:
        return False
    raise ValidationError(f"{label} must be yes or no.")


