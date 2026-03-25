from __future__ import annotations

import asyncio
from calendar import month_name
from dataclasses import dataclass, replace
from typing import Final, Literal

import discord

from bdayblaze.discord.announcements import build_announcement_message
from bdayblaze.discord.embed_budget import BudgetedEmbed, code_block_snippet, truncate_text
from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNIVERSARY_TEMPLATE,
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    DEFAULT_DM_TEMPLATE,
    default_template_for_kind,
    preview_context_for_kind,
    supported_placeholder_groups,
)
from bdayblaze.domain.media_validation import (
    assess_media_url,
    mark_validated_direct_media_url,
    strip_validated_direct_media_marker,
)
from bdayblaze.domain.announcement_theme import (
    announcement_theme_description,
    announcement_theme_label,
    supported_announcement_themes,
)
from bdayblaze.domain.models import (
    AnnouncementStudioPresentation,
    GuildSettings,
    RecurringCelebration,
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
from bdayblaze.services.media_validation_service import MediaProbeResult, probe_media_url
from bdayblaze.services.settings_service import SettingsService

_SETUP_TITLE: Final = "🛠 Birthday Setup"
_STUDIO_TITLE: Final = "✨ Celebration Studio"
_UI_UNSET: Final = object()
_UI_LOGGER = get_logger(component="celebration_studio")
SectionName = Literal[
    "home",
    "birthday",
    "birthday_dm",
    "anniversary",
    "server_anniversary",
    "events",
    "help",
]
_SECTION_LABELS: Final[dict[SectionName, str]] = {
    "home": "🏠 Studio overview",
    "birthday": "🎂 Birthday announcement",
    "birthday_dm": "💌 Birthday DM",
    "anniversary": "🎉 Member anniversary",
    "server_anniversary": "🏰 Server anniversary",
    "events": "📅 Custom annual events",
    "help": "🧩 Studio help",
}


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


def build_setup_embed(settings: GuildSettings, note: str | None = None) -> discord.Embed:
    anniversary_channel = settings.anniversary_channel_id or settings.announcement_channel_id
    budget = BudgetedEmbed.create(
        title=_SETUP_TITLE,
        description=(
            "Control routing, timezone, eligibility, and delivery safeguards before anything "
            "goes live."
        ),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="🎂 Birthday announcement",
        value=(
            f"Status: {_format_enabled(settings.announcements_enabled)}\n"
            f"Birthday channel: {_format_channel(settings.announcement_channel_id)}\n"
            f"Theme: {announcement_theme_label(settings.announcement_theme)}\n"
            f"Celebration style: {settings.celebration_mode.title()}"
        ),
        inline=False,
    )
    budget.add_field(
        name="🧱 Eligibility and anti-spam",
        value=(
            "Eligibility role: "
            f"{_format_eligibility_role(settings.eligibility_role_id)}\n"
            f"Ignore bots: {_format_enabled(settings.ignore_bots)}\n"
            f"Minimum membership age: {settings.minimum_membership_days} day(s)\n"
            f"Mention suppression threshold: {settings.mention_suppression_threshold}"
        ),
        inline=False,
    )
    budget.add_field(
        name="💌 Roles and private delivery",
        value=(
            "Birthday role: "
            f"{_format_enabled(settings.role_enabled)} "
            f"({_format_role(settings.birthday_role_id)})\n"
            f"Birthday DM: {_format_enabled(settings.birthday_dm_enabled)}"
        ),
        inline=False,
    )
    budget.add_field(
        name="🎉 Anniversary routing",
        value=(
            f"Member anniversaries: {_format_enabled(settings.anniversary_enabled)}\n"
            f"Anniversary channel: {_format_channel(anniversary_channel)}\n"
            "Model: tracked members only"
        ),
        inline=False,
    )
    budget.add_field(
        name="🌍 Default timezone",
        value=(
            f"Saved: `{settings.default_timezone}`\n"
            f"Examples: {timezone_guidance(allow_server_default=False)}"
        ),
        inline=False,
    )
    if note:
        budget.add_field(name="✅ Updated", value=note, inline=False)
    budget.add_field(
        name="Studio safety",
        value=(
            "Blocked-attempt audit log: "
            f"{_format_channel(settings.studio_audit_channel_id)}\n"
            "Unsafe message text, event names, and media URLs are blocked."
        ),
        inline=False,
    )
    budget.set_footer(
        "Open Celebration Studio from this panel to manage copy, media, previews, and resets."
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
    note: str | None = None,
    image_probe: MediaProbeResult | None = None,
    thumbnail_probe: MediaProbeResult | None = None,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Media Tools",
        description=(
            "Save only direct image, GIF, or WebP asset URLs here. Regular webpages are not "
            "used as embed images."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="Updated", value=note, inline=False)
    budget.add_line_fields(
        "Current media",
        (
            _media_state_line(
                settings.announcement_image_url,
                label="Announcement image",
                probe=image_probe,
            ),
            _media_state_line(
                settings.announcement_thumbnail_url,
                label="Announcement thumbnail",
                probe=thumbnail_probe,
            ),
        ),
        inline=False,
    )
    budget.add_field(
        name="Validation flow",
        value=(
            "Edit media validates both URLs before save. Use Validate current to re-check saved "
            "URLs without changing them."
        ),
        inline=False,
    )
    budget.add_field(
        name="Reset behavior",
        value="Reset media clears only the shared image and thumbnail fields.",
        inline=False,
    )
    budget.add_field(
        name="State guide",
        value=(
            "Likely direct media: Discord preview should usually work.\n"
            "Webpage URL: Discord will not render that page as an image.\n"
            "Needs validation: use Validate current before trusting it.\n"
            "Invalid or unsafe: replace the URL before saving.\n"
            "Validation unavailable: the probe could not confirm it right now."
        ),
        inline=False,
    )
    return budget.build()


def build_message_template_embed(
    settings: GuildSettings,
    *,
    note: str | None = None,
    section: SectionName = "home",
    guild: discord.Guild | None = None,
    server_anniversary: RecurringCelebration | None = None,
    recurring_events: tuple[RecurringCelebration, ...] = (),
) -> discord.Embed:
    state = _server_anniversary_state(guild=guild, celebration=server_anniversary)
    budget = BudgetedEmbed.create(
        title=(
            _STUDIO_TITLE
            if section == "home"
            else f"{_STUDIO_TITLE} · {_SECTION_LABELS[section]}"
        ),
        description=_section_description(section),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="📍 Current focus",
        value=_SECTION_LABELS[section],
        inline=False,
    )
    if note:
        budget.add_field(name="✅ Update", value=note, inline=False)

    if section == "home":
        budget.add_field(
            name="🎂 Birthday announcement",
            value=(
                f"Status: {_format_enabled(settings.announcements_enabled)}\n"
                f"Channel: {_format_channel(settings.announcement_channel_id)}\n"
                "Copy length: "
                f"{len(settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE)} chars"
            ),
            inline=False,
        )
        budget.add_field(
            name="💌 Birthday DM",
            value=(
                f"Status: {_format_enabled(settings.birthday_dm_enabled)}\n"
                f"Copy length: {len(settings.birthday_dm_template or DEFAULT_DM_TEMPLATE)} chars\n"
                "Delivery: best effort private DM"
            ),
            inline=False,
        )
        budget.add_field(
            name="🎉 Member anniversary",
            value=(
                f"Status: {_format_enabled(settings.anniversary_enabled)}\n"
                "Channel: "
                f"{_format_channel(_effective_anniversary_channel(settings))}\n"
                "Model: tracked members only"
            ),
            inline=False,
        )
        budget.add_field(
            name="🏰 Server anniversary",
            value=(
                f"Status: {_format_enabled(state.enabled)}\n"
                f"Date: {_format_month_day(state.month, state.day)}\n"
                "Source: "
                f"{'Guild creation date' if state.use_guild_created_date else 'Custom date'}"
            ),
            inline=False,
        )
        budget.add_line_fields("🎨 Shared visuals", _presentation_lines(settings), inline=False)
        if recurring_events:
            budget.add_line_fields(
                "📅 Custom annual events",
                [_format_event_line(celebration) for celebration in recurring_events[:4]],
                inline=False,
            )
        else:
            budget.add_field(
                name="📅 Custom annual events",
                value="No custom annual events are configured yet.",
                inline=False,
            )
    elif section == "help":
        for group_name, placeholders in supported_placeholder_groups():
            budget.add_line_fields(
                group_name,
                [f"`{{{name}}}` - {description}" for name, description in placeholders],
                inline=False,
            )
        budget.add_field(
            name="🖼 Media URL examples",
            value=(
                "`https://cdn.example.com/birthday/banner.gif`\n"
                "`https://images.example.com/render?id=42&sig=abc123`\n"
                "`https://media.example.com/assets/celebration`\n"
                "`https://www.example.com/gallery/photo-42` is a webpage, not direct media."
            ),
            inline=False,
        )
        budget.add_field(
            name="🧪 Preview and reset notes",
            value=(
                "Media Tools validates image and thumbnail URLs before save.\n"
                "Signed, query-string, and extensionless URLs can work when validation proves "
                "they are direct media assets.\n"
                "Full preview is still the final Discord render check. It never pings members.\n"
                "Reset copy restores the default template. Reset media clears only image and "
                "thumbnail fields. Shared visuals now cover title, footer, and accent color."
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
                f"Announcement channel: {_format_channel(settings.announcement_channel_id)}",
                f"Theme: {announcement_theme_label(settings.announcement_theme)}",
                f"Celebration style: {settings.celebration_mode.title()}",
            ),
            field_label="🎂 Birthday announcement copy",
        )
    elif section == "birthday_dm":
        budget.add_field(
            name="💌 Birthday DM copy",
            value=code_block_snippet(settings.birthday_dm_template or DEFAULT_DM_TEMPLATE),
            inline=False,
        )
        budget.add_line_fields(
            "🔁 Routing and behavior",
            (
                f"Live status: {_format_enabled(settings.birthday_dm_enabled)}",
                "Delivery model: best effort private DM",
                "Previews stay private and never ping anyone.",
            ),
            inline=False,
        )
        budget.add_line_fields(
            "🎨 Theme coverage",
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
                "Channel: "
                f"{_format_channel(_effective_anniversary_channel(settings))}",
                "Model: tracked members only",
            ),
            field_label="🎉 Member anniversary copy",
        )
    elif section == "server_anniversary":
        budget.add_line_fields(
            "🏰 Schedule and routing",
            (
                f"Live status: {_format_enabled(state.enabled)}",
                f"Date: {_format_month_day(state.month, state.day)}",
                "Date source: "
                f"{'Guild creation date' if state.use_guild_created_date else 'Custom date'}",
                f"Channel: {_format_channel(state.channel_id or settings.announcement_channel_id)}",
            ),
            inline=False,
        )
        budget.add_field(
            name="📝 Server anniversary copy",
            value=code_block_snippet(
                state.template or default_template_for_kind("server_anniversary")
            ),
            inline=False,
        )
        budget.add_line_fields("🎨 Shared visuals", _presentation_lines(settings), inline=False)
    else:
        if recurring_events:
            budget.add_line_fields(
                "📅 Configured events",
                [_format_event_line(celebration) for celebration in recurring_events],
                inline=False,
            )
        else:
            budget.add_field(
                name="📅 Configured events",
                value="No custom annual events are configured yet.",
                inline=False,
            )
        budget.add_field(
            name="🧭 Managing events",
            value=(
                "Use `/birthday event add`, `/birthday event edit`, and "
                "`/birthday event list` for direct event management.\n"
                "Use `/birthday test-message kind:recurring_event` with an event id to dry-run one."
            ),
            inline=False,
        )

    budget.set_footer(
        "Use the section menu to move between public birthdays, DMs, anniversaries, "
        "and yearly events."
    )
    return budget.build()


def _add_delivery_section(
    budget: BudgetedEmbed,
    *,
    settings: GuildSettings,
    template: str,
    routing_lines: tuple[str, ...],
    field_label: str,
) -> None:
    budget.add_field(name=field_label, value=code_block_snippet(template), inline=False)
    budget.add_line_fields("📡 Routing and behavior", routing_lines, inline=False)
    budget.add_line_fields("🎨 Shared visuals", _presentation_lines(settings), inline=False)


def _presentation_lines(settings: GuildSettings) -> tuple[str, ...]:
    return (
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        f"Theme note: {announcement_theme_description(settings.announcement_theme)}",
        f"Title override: {settings.announcement_title_override or 'Default'}",
        f"Footer text: {settings.announcement_footer_text or 'Default'}",
        _media_state_line(settings.announcement_image_url, label="Announcement image"),
        _media_state_line(settings.announcement_thumbnail_url, label="Announcement thumbnail"),
        f"Accent color: {_format_accent_color(settings.announcement_accent_color)}",
    )


def _media_state_line(
    value: str | None,
    *,
    label: str,
    probe: MediaProbeResult | None = None,
) -> str:
    if value is None or not value.strip():
        return f"{label}: Not set"
    if probe is not None:
        return f"{label}: {probe.status_label()} | {truncate_text(probe.url, 72)}"
    assessment = assess_media_url(value, label=label)
    if assessment is None:
        return f"{label}: Not set"
    display_url = strip_validated_direct_media_marker(assessment.normalized_url)
    return f"{label}: {assessment.status_label()} | {truncate_text(display_url or '', 72)}"


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
        f"Style: {settings.celebration_mode.title()}",
        "Shared title, footer, image, thumbnail, and accent overrides stay on public "
        "announcement surfaces.",
    )


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
            "Everything that shapes live celebrations lives here: copy, shared visuals, routing "
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
            "Tracked join anniversaries reuse shared visuals, but keep separate copy and routing."
        ),
        "server_anniversary": (
            "Treat the server birthday as a first-class annual celebration with explicit "
            "date, routing, and preview controls."
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
        f"`{celebration.id}` {celebration.name} • {month_name[celebration.event_month]} "
        f"{celebration.event_day} • {_format_enabled(celebration.enabled)} • "
        f"{_format_channel(celebration.channel_id)}"
    )


def _effective_anniversary_channel(settings: GuildSettings) -> int | None:
    return settings.anniversary_channel_id or settings.announcement_channel_id


def build_server_anniversary_control_embed(
    settings: GuildSettings,
    *,
    guild: discord.Guild,
    celebration: RecurringCelebration | None,
    note: str | None = None,
) -> discord.Embed:
    state = _server_anniversary_state(guild=guild, celebration=celebration)
    budget = BudgetedEmbed.create(
        title="🏰 Server Anniversary Controls",
        description=(
            "Choose live status, date source, and channel routing without raw text inputs."
        ),
        color=discord.Color.blurple(),
    )
    if note:
        budget.add_field(name="✅ Update", value=note, inline=False)
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
        "Routing",
        (
            f"Channel override: {_format_channel(state.channel_id)}",
            "Live route: "
            f"{_format_channel(state.channel_id or settings.announcement_channel_id)}",
        ),
        inline=False,
    )
    budget.add_field(
        name="🧪 Preview and copy",
        value=(
            "Use Preview below to see the exact current render.\n"
            "Server anniversary copy still lives in the main Studio section."
        ),
        inline=False,
    )
    budget.add_line_fields("🎨 Shared visuals", _presentation_lines(settings), inline=False)
    budget.set_footer("Use the controls below, then return to Celebration Studio.")
    return budget.build()


class SetupView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild | None = None,
        birthday_service: BirthdayService | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.add_item(AnnouncementChannelSelect(self))
        self.add_item(BirthdayRoleSelect(self))
        self.add_item(EligibilityRoleSelect(self))

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
        await interaction.response.edit_message(
            embed=build_setup_embed(latest, note),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
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
        await interaction.response.send_modal(
            MembershipRulesModal(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
                birthday_service=self.birthday_service,
            )
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
        server_anniversary, recurring_events = await _load_studio_context(
            interaction.guild,
            self.birthday_service,
        )
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                section="home",
                guild=interaction.guild,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
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
            await self.setup_view.settings_service.update_settings(
                interaction.guild,
                announcement_channel_id=channel_id,
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
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild | None = None,
        birthday_service: BirthdayService | None = None,
        section: SectionName = "home",
        server_anniversary: RecurringCelebration | None = None,
        recurring_events: tuple[RecurringCelebration, ...] = (),
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.birthday_service = birthday_service
        self.section = section
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
    ) -> tuple[GuildSettings, RecurringCelebration | None, tuple[RecurringCelebration, ...]]:
        latest = await self.settings_service.get_settings(guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            guild,
            self.birthday_service,
        )
        return latest, server_anniversary, recurring_events

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        section: SectionName | None = None,
        note: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        latest, server_anniversary, recurring_events = await self._latest_context(
            interaction.guild
        )
        next_section = section or self.section
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                note=note,
                section=next_section,
                guild=interaction.guild,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
                section=next_section,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
        )

    def _configure_buttons(self) -> None:
        if self.section == "birthday":
            self.edit_primary.label = "Edit birthday copy"
            self.edit_secondary.label = "Edit shared visuals"
            self.preview_current.label = "Preview birthday"
            self.reset_current.label = "Reset birthday copy"
        elif self.section == "birthday_dm":
            self.edit_primary.label = "Edit DM copy"
            self.edit_secondary.label = "Edit announcement visuals"
            self.preview_current.label = "Preview DM"
            self.reset_current.label = "Reset DM copy"
        elif self.section == "anniversary":
            self.edit_primary.label = "Edit anniversary copy"
            self.edit_secondary.label = "Edit shared visuals"
            self.preview_current.label = "Preview anniversary"
            self.reset_current.label = "Reset anniversary copy"
        elif self.section == "server_anniversary":
            self.edit_primary.label = "Schedule controls"
            self.edit_secondary.label = "Edit event copy"
            self.preview_current.label = "Preview server anniversary"
            self.reset_current.label = "Reset to guild date"
        elif self.section == "events":
            self.edit_primary.label = "Event commands"
            self.edit_secondary.label = "Edit shared visuals"
            self.preview_current.label = "Preview first event"
            self.reset_current.label = "Reset shared visuals"
            self.preview_current.disabled = len(self.recurring_events) == 0
        elif self.section == "help":
            self.edit_primary.disabled = True
            self.edit_secondary.disabled = True
            self.preview_current.disabled = True
            self.reset_current.disabled = True
            self.reset_media.disabled = True
        else:
            self.edit_primary.label = "Open birthday copy"
            self.edit_secondary.label = "Edit shared visuals"
            self.preview_current.label = "Preview birthday"
            self.reset_current.label = "Reset shared visuals"

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
        latest, server_anniversary, recurring_events = await self._latest_context(
            interaction.guild
        )
        if self.section == "server_anniversary":
            await interaction.response.send_message(
                embed=build_server_anniversary_control_embed(
                    latest,
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
                "Use `/birthday test-message` with `kind: recurring_event` and an event id "
                "to dry-run one.",
                ephemeral=True,
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
        latest, server_anniversary, _ = await self._latest_context(interaction.guild)
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
        latest, server_anniversary, recurring_events = await self._latest_context(interaction.guild)
        try:
            status_embed, preview_embed = await _build_studio_preview_pair(
                guild=interaction.guild,
                settings=latest,
                settings_service=self.settings_service,
                section=self.section,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            embeds=[status_embed, preview_embed],
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
        latest, server_anniversary, recurring_events = await self._latest_context(interaction.guild)
        try:
            note = await self._reset_section(
                interaction.guild,
                server_anniversary=server_anniversary,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest, server_anniversary, recurring_events = await self._latest_context(interaction.guild)
        await interaction.response.send_message(
            embed=_build_return_embed("Celebration Studio updated", note),
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
        await interaction.response.send_message(
            embed=build_media_tools_embed(
                await self.settings_service.get_settings(interaction.guild.id),
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=await self.settings_service.get_settings(interaction.guild.id),
                owner_id=self.owner_id,
                guild=interaction.guild,
                section=self.section,
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
        await interaction.response.edit_message(
            embed=build_setup_embed(latest),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                birthday_service=self.birthday_service,
            ),
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
            announcement_image_url=None,
            announcement_thumbnail_url=None,
            announcement_accent_color=None,
        )
        return "Shared visual presentation reset to the current theme preset."


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
        server_anniversary: RecurringCelebration | None,
        recurring_events: tuple[RecurringCelebration, ...],
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.section = section
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
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                section=self.section,
                guild=self.guild,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section=self.section,
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
        await interaction.response.edit_message(
            embed=build_setup_embed(latest),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
            ),
        )


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
        await interaction.response.edit_message(
            embed=build_setup_embed(latest),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
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


class MembershipRulesModal(AdminPanelModal, title="Membership and anti-spam rules"):
    minimum_days: discord.ui.TextInput[MembershipRulesModal] = discord.ui.TextInput(
        label="Minimum membership age (days)",
        required=True,
        max_length=4,
    )
    mention_threshold: discord.ui.TextInput[MembershipRulesModal] = discord.ui.TextInput(
        label="Mention suppression threshold",
        required=True,
        max_length=2,
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
        birthday_service: BirthdayService | None,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        self.minimum_days.default = str(settings.minimum_membership_days)
        self.mention_threshold.default = str(settings.mention_suppression_threshold)

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
                minimum_membership_days=int(self.minimum_days.value),
                mention_suppression_threshold=int(self.mention_threshold.value),
            )
        except (ValidationError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=_build_return_embed(
                "Membership rules saved",
                "Eligibility and mention-throttling rules were updated.",
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


class StudioPresentationModal(AdminPanelModal, title="Celebration visuals"):
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
                "Celebration visuals saved",
                "Shared title, footer, and accent color were updated.",
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


class MediaEditModal(AdminPanelModal, title="Update shared media"):
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
        owner_id: int,
        birthday_service: BirthdayService | None,
        guild: discord.Guild,
        section: SectionName,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.birthday_service = birthday_service
        self.guild = guild
        self.section = section
        self.image_url.default = strip_validated_direct_media_marker(
            settings.announcement_image_url
        ) or ""
        self.thumbnail_url.default = strip_validated_direct_media_marker(
            settings.announcement_thumbnail_url
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
        image_result, thumbnail_result = await asyncio.gather(
            probe_media_url(image_value, label="Announcement image"),
            probe_media_url(thumbnail_value, label="Announcement thumbnail"),
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
            await interaction.response.send_message(
                embed=build_media_tools_embed(
                    replace(
                        self.settings,
                        announcement_image_url=image_value,
                        announcement_thumbnail_url=thumbnail_value,
                    ),
                    note="No changes were saved.",
                    image_probe=image_result,
                    thumbnail_probe=thumbnail_result,
                ),
                view=StudioMediaView(
                    settings_service=self.settings_service,
                    birthday_service=self.birthday_service,
                    settings=self.settings,
                    owner_id=self.owner_id,
                    guild=self.guild,
                    section=self.section,
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
            announcement_image_url=saved_image_value,
            announcement_thumbnail_url=saved_thumbnail_value,
        )
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_media_tools_embed(
                latest,
                note="Shared media saved.",
                image_probe=image_result,
                thumbnail_probe=thumbnail_result,
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                section=self.section,
            ),
            ephemeral=True,
        )


class StudioMediaView(AdminPanelView):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
        section: SectionName,
    ) -> None:
        super().__init__(timeout=600)
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.section = section

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "These media tools belong to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Edit media", style=discord.ButtonStyle.primary, row=0)
    async def edit_media(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        await interaction.response.send_modal(
            MediaEditModal(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                birthday_service=self.birthday_service,
                guild=self.guild,
                section=self.section,
            )
        )

    @discord.ui.button(label="Validate current", style=discord.ButtonStyle.secondary, row=0)
    async def validate_current(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        latest = await self.settings_service.get_settings(self.guild.id)
        image_result, thumbnail_result = await asyncio.gather(
            probe_media_url(
                strip_validated_direct_media_marker(latest.announcement_image_url),
                label="Announcement image",
            ),
            probe_media_url(
                strip_validated_direct_media_marker(latest.announcement_thumbnail_url),
                label="Announcement thumbnail",
            ),
        )
        await interaction.response.edit_message(
            embed=build_media_tools_embed(
                latest,
                note="Current shared media was validated.",
                image_probe=image_result,
                thumbnail_probe=thumbnail_result,
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                section=self.section,
            ),
        )

    @discord.ui.button(label="Reset media", style=discord.ButtonStyle.danger, row=0)
    async def reset_media(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        await self.settings_service.update_validated_media(
            self.guild,
            announcement_image_url=None,
            announcement_thumbnail_url=None,
        )
        latest = await self.settings_service.get_settings(self.guild.id)
        await interaction.response.edit_message(
            embed=build_media_tools_embed(
                latest,
                note="Shared image and thumbnail media were cleared.",
            ),
            view=StudioMediaView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                section=self.section,
            ),
        )

    @discord.ui.button(label="Back to studio", style=discord.ButtonStyle.secondary, row=1)
    async def back_to_studio(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[StudioMediaView],
    ) -> None:
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        latest = await self.settings_service.get_settings(self.guild.id)
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                section=self.section,
                guild=self.guild,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section=self.section,
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
    ) -> tuple[GuildSettings, RecurringCelebration | None, tuple[RecurringCelebration, ...]]:
        latest = await self.settings_service.get_settings(self.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        return latest, server_anniversary, recurring_events

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        note: str | None = None,
    ) -> None:
        latest, server_anniversary, recurring_events = await self._latest_context()
        await interaction.response.edit_message(
            embed=build_server_anniversary_control_embed(
                latest,
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
            channel_id=target_channel_id,  # type: ignore[arg-type]
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
        latest, server_anniversary, _ = await self._latest_context()
        await interaction.response.send_modal(
            ServerAnniversaryDateModal(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                celebration=server_anniversary,
            )
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
        latest, server_anniversary, recurring_events = await self._latest_context()
        status_embed, preview_embed = await _build_studio_preview_pair(
            guild=self.guild,
            settings=latest,
            settings_service=self.settings_service,
            section="server_anniversary",
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
        latest, server_anniversary, recurring_events = await self._latest_context()
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                section="server_anniversary",
                guild=self.guild,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=self.guild,
                birthday_service=self.birthday_service,
                section="server_anniversary",
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
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
        server_anniversary, recurring_events = await _load_studio_context(
            self.guild,
            self.birthday_service,
        )
        await interaction.response.edit_message(
            embed=build_server_anniversary_control_embed(
                latest,
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


class ServerAnniversaryDateModal(AdminPanelModal, title="Set custom server anniversary date"):
    month_input: discord.ui.TextInput[ServerAnniversaryDateModal] = discord.ui.TextInput(
        label="Month",
        required=True,
        max_length=2,
        placeholder="3",
    )
    day_input: discord.ui.TextInput[ServerAnniversaryDateModal] = discord.ui.TextInput(
        label="Day",
        required=True,
        max_length=2,
        placeholder="25",
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild,
        celebration: RecurringCelebration | None,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        state = _server_anniversary_state(guild=guild, celebration=celebration)
        if state.month is not None:
            self.month_input.default = str(state.month)
        if state.day is not None:
            self.day_input.default = str(state.day)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.birthday_service is None:
            await interaction.response.send_message(
                "Server anniversary tools are not available in this panel.",
                ephemeral=True,
            )
            return
        try:
            month = _parse_optional_int(self.month_input.value, label="Month")
            day = _parse_optional_int(self.day_input.value, label="Day")
            existing = await self.birthday_service.get_server_anniversary(self.guild.id)
            await self.birthday_service.upsert_server_anniversary(
                guild_id=self.guild.id,
                guild_created_at_utc=self.guild.created_at,
                override_month=month,
                override_day=day,
                channel_id=existing.channel_id if existing is not None else None,
                template=existing.template if existing is not None else None,
                enabled=existing.enabled if existing is not None else False,
                use_guild_created_date=False,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
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
            ephemeral=True,
        )


async def _build_studio_preview_pair(
    *,
    guild: discord.Guild,
    settings: GuildSettings,
    settings_service: SettingsService,
    section: SectionName,
    server_anniversary: RecurringCelebration | None,
    recurring_events: tuple[RecurringCelebration, ...],
) -> tuple[discord.Embed, discord.Embed]:
    if section == "help":
        raise ValidationError("Select a delivery section before previewing.")
    if section in {"home", "birthday"}:
        preview = preview_context_for_kind("birthday_announcement")
        route = _format_channel(settings.announcement_channel_id)
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
                presentation=settings.presentation(),
                template=settings.announcement_template,
                preview_label="Preview only - birthday announcement",
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    section=section,
                    route=route,
                    mention_suppressed=(
                        len(preview.recipients) >= settings.mention_suppression_threshold
                    ),
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    elif section == "birthday_dm":
        preview = preview_context_for_kind("birthday_dm")
        route = "Private DM"
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
                    section=section,
                    route=route,
                    mention_suppressed=False,
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    elif section == "anniversary":
        preview = preview_context_for_kind("anniversary")
        readiness = await settings_service.describe_delivery(guild, kind="anniversary")
        route = _format_channel(_effective_anniversary_channel(settings))
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
                presentation=settings.presentation(),
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
                    section=section,
                    route=route,
                    mention_suppressed=(
                        len(preview.recipients) >= settings.mention_suppression_threshold
                    ),
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    elif section == "server_anniversary":
        state = _server_anniversary_state(guild=guild, celebration=server_anniversary)
        if state.month is None or state.day is None:
            raise ValidationError(
                "Discord did not provide the guild creation date. "
                "Save a custom server-anniversary date first."
            )
        readiness = await settings_service.describe_delivery(
            guild,
            kind="server_anniversary",
            channel_id=state.channel_id,
        )
        route = _format_channel(state.channel_id or settings.announcement_channel_id)
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
                presentation=settings.presentation(),
                template=state.template,
                preview_label="Preview only - server anniversary",
                event_name=state.name,
                event_month=state.month,
                event_day=state.day,
            ).embed
        except (ValidationError, ValueError) as exc:
            return (
                _build_preview_status_embed(
                    settings,
                    readiness,
                    section=section,
                    route=route,
                    mention_suppressed=False,
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    else:
        if not recurring_events:
            raise ValidationError("Create a recurring annual event before previewing one here.")
        celebration = recurring_events[0]
        readiness = await settings_service.describe_delivery(
            guild,
            kind="recurring_event",
            channel_id=celebration.channel_id,
        )
        route = _format_channel(celebration.channel_id or settings.announcement_channel_id)
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
                presentation=settings.presentation(),
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
                    section=section,
                    route=route,
                    mention_suppressed=False,
                    preview_error=str(exc),
                ),
                _build_preview_unavailable_embed(_SECTION_LABELS[section], str(exc)),
            )
    return (
        _build_preview_status_embed(
            settings,
            readiness,
            section=section,
            route=route,
            mention_suppressed=(
                len(preview.recipients) >= settings.mention_suppression_threshold
                if section in {"home", "birthday", "anniversary"}
                else False
            ),
        ),
        preview_embed,
    )


def _build_preview_status_embed(
    settings: GuildSettings,
    readiness: object,
    *,
    section: SectionName,
    route: str,
    mention_suppressed: bool,
    preview_error: str | None = None,
) -> discord.Embed:
    from bdayblaze.domain.models import AnnouncementDeliveryReadiness

    assert isinstance(readiness, AnnouncementDeliveryReadiness)
    presentation = _preview_presentation_for_section(settings, section=section)
    media_diagnostics = build_presentation_diagnostics(presentation)
    budget = BudgetedEmbed.create(
        title="🧪 Dry-Run Preview",
        description="Preview only. No live celebration was sent.",
        color=discord.Color.green() if readiness.status == "ready" else discord.Color.orange(),
    )
    budget.add_field(name="Preview surface", value=_SECTION_LABELS[section], inline=False)
    budget.add_field(name="Live delivery readiness", value=readiness.summary, inline=False)
    if readiness.details:
        budget.add_line_fields("Details", readiness.details, inline=False)
    budget.add_line_fields(
        "Routing and mentions",
        (
            f"Live route: {route}",
            _preview_mention_status(section=section, mention_suppressed=mention_suppressed),
        ),
        inline=False,
    )
    budget.add_line_fields(
        "Media and visuals",
        _preview_visual_lines(
            settings,
            section=section,
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
    section: SectionName,
    mention_suppressed: bool,
) -> str:
    if section == "birthday_dm":
        return "Mentions: not used in private DMs."
    if section in {"server_anniversary", "events"}:
        return "Mentions: not used for this celebration type."
    if mention_suppressed:
        return "Mentions: would be suppressed for a batch this size."
    return "Mentions: would be allowed for a small live batch."


def _preview_presentation_for_section(
    settings: GuildSettings,
    *,
    section: SectionName,
) -> AnnouncementStudioPresentation:
    kind = {
        "home": "birthday_announcement",
        "birthday": "birthday_announcement",
        "birthday_dm": "birthday_dm",
        "anniversary": "anniversary",
        "server_anniversary": "server_anniversary",
        "events": "recurring_event",
        "help": "birthday_announcement",
    }[section]
    return settings.presentation_for_kind(kind)


def _preview_visual_lines(
    settings: GuildSettings,
    *,
    section: SectionName,
    media_diagnostics: tuple[object, ...],
) -> tuple[str, ...]:
    if section == "birthday_dm":
        return (
            "Media status: Not used for live birthday DMs",
            f"Theme: {announcement_theme_label(settings.announcement_theme)}",
            f"Style: {settings.celebration_mode.title()}",
            "Shared title, footer, image, thumbnail, and accent overrides stay on public "
            "announcement surfaces.",
        )
    return (
        f"Media status: {'Ready' if not media_diagnostics else 'Needs attention'}",
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        f"Style: {settings.celebration_mode.title()}",
        f"Title override: {settings.announcement_title_override or 'Default'}",
        _media_state_line(settings.announcement_image_url, label="Image"),
        _media_state_line(settings.announcement_thumbnail_url, label="Thumbnail"),
    )


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
