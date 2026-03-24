from __future__ import annotations

from calendar import month_name
from dataclasses import dataclass
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
from bdayblaze.domain.announcement_theme import (
    announcement_theme_description,
    announcement_theme_label,
    supported_announcement_themes,
)
from bdayblaze.domain.models import GuildSettings, RecurringCelebration
from bdayblaze.domain.timezones import timezone_guidance
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.settings_service import SettingsService

_SETUP_TITLE: Final = "Birthday setup"
_STUDIO_TITLE: Final = "Celebration Studio"
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
    "home": "Studio home",
    "birthday": "Birthday announcement",
    "birthday_dm": "Birthday DM",
    "anniversary": "Member anniversary",
    "server_anniversary": "Server anniversary",
    "events": "Custom annual events",
    "help": "Template help",
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
        _UI_LOGGER.warning(
            "celebration_studio_http_error",
            surface=surface,
            guild_id=guild_hash,
            user_id=user_hash,
            status=error.status,
            discord_code=error.code,
        )
        message = (
            "Discord rejected that UI response. Try refreshing the panel or shortening the "
            "current content."
        )
        if is_admin and error.status == 400:
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
            "Control routing, timezones, eligibility rules, and operator safeguards before "
            "birthday or anniversary messages go live."
        ),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="Announcements",
        value=(
            f"Status: {_format_enabled(settings.announcements_enabled)}\n"
            f"Birthday channel: {_format_channel(settings.announcement_channel_id)}\n"
            f"Theme: {announcement_theme_label(settings.announcement_theme)}\n"
            f"Celebration style: {settings.celebration_mode.title()}"
        ),
        inline=False,
    )
    budget.add_field(
        name="Eligibility and anti-spam",
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
        name="Roles and private delivery",
        value=(
            "Birthday role: "
            f"{_format_enabled(settings.role_enabled)} "
            f"({_format_role(settings.birthday_role_id)})\n"
            f"Birthday DM: {_format_enabled(settings.birthday_dm_enabled)}"
        ),
        inline=False,
    )
    budget.add_field(
        name="Anniversaries",
        value=(
            f"Member anniversaries: {_format_enabled(settings.anniversary_enabled)}\n"
            f"Anniversary channel: {_format_channel(anniversary_channel)}\n"
            "Model: tracked members only"
        ),
        inline=False,
    )
    budget.add_field(
        name="Default timezone",
        value=(
            f"Saved: `{settings.default_timezone}`\n"
            f"Examples: {timezone_guidance(allow_server_default=False)}"
        ),
        inline=False,
    )
    if note:
        budget.add_field(name="Saved", value=note, inline=False)
    budget.set_footer(
        "Open Celebration Studio from this panel to manage templates, media, and previews."
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
        title=_STUDIO_TITLE,
        description=_section_description(section),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="Current section",
        value=_SECTION_LABELS[section],
        inline=False,
    )
    if note:
        budget.add_field(name="Update", value=note, inline=False)

    if section == "home":
        budget.add_field(
            name="Birthday announcement",
            value=(
                f"Status: {_format_enabled(settings.announcements_enabled)}\n"
                f"Channel: {_format_channel(settings.announcement_channel_id)}\n"
                "Copy length: "
                f"{len(settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE)} chars"
            ),
            inline=False,
        )
        budget.add_field(
            name="Birthday DM",
            value=(
                f"Status: {_format_enabled(settings.birthday_dm_enabled)}\n"
                f"Copy length: {len(settings.birthday_dm_template or DEFAULT_DM_TEMPLATE)} chars\n"
                "Delivery: best effort private DM"
            ),
            inline=False,
        )
        budget.add_field(
            name="Member anniversaries",
            value=(
                f"Status: {_format_enabled(settings.anniversary_enabled)}\n"
                "Channel: "
                f"{_format_channel(_effective_anniversary_channel(settings))}\n"
                "Model: tracked members only"
            ),
            inline=False,
        )
        budget.add_field(
            name="Server anniversary",
            value=(
                f"Status: {_format_enabled(state.enabled)}\n"
                f"Date: {_format_month_day(state.month, state.day)}\n"
                "Source: "
                f"{'Guild creation date' if state.use_guild_created_date else 'Custom date'}"
            ),
            inline=False,
        )
        budget.add_line_fields("Visual style", _presentation_lines(settings), inline=False)
        if recurring_events:
            budget.add_line_fields(
                "Custom annual events",
                [_format_event_line(celebration) for celebration in recurring_events[:4]],
                inline=False,
            )
        else:
            budget.add_field(
                name="Custom annual events",
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
            name="Media and preview notes",
            value=(
                "Image and thumbnail URLs must use HTTPS and point to PNG, JPG, JPEG, GIF, "
                "or WEBP files.\n"
                "Previews never ping members and only show what Discord would render.\n"
                "Long templates are trimmed in studio summaries but saved in full after validation."
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
            field_label="Birthday announcement template",
        )
    elif section == "birthday_dm":
        _add_delivery_section(
            budget,
            settings=settings,
            template=settings.birthday_dm_template or DEFAULT_DM_TEMPLATE,
            routing_lines=(
                f"Live status: {_format_enabled(settings.birthday_dm_enabled)}",
                "Delivery model: best effort private DM",
                "Previews stay private and never ping anyone.",
            ),
            field_label="Birthday DM template",
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
            field_label="Member anniversary template",
        )
    elif section == "server_anniversary":
        budget.add_line_fields(
            "Schedule and routing",
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
            name="Server anniversary template",
            value=code_block_snippet(
                state.template or default_template_for_kind("server_anniversary")
            ),
            inline=False,
        )
        budget.add_line_fields("Shared visuals", _presentation_lines(settings), inline=False)
    else:
        if recurring_events:
            budget.add_line_fields(
                "Configured events",
                [_format_event_line(celebration) for celebration in recurring_events],
                inline=False,
            )
        else:
            budget.add_field(
                name="Configured events",
                value="No custom annual events are configured yet.",
                inline=False,
            )
        budget.add_field(
            name="Managing events",
            value=(
                "Use `/birthday event add`, `/birthday event edit`, and "
                "`/birthday event list` for direct event management."
            ),
            inline=False,
        )

    budget.set_footer(
        "Use the section menu to move between birthdays, DMs, anniversaries, and annual events."
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
    budget.add_line_fields("Routing and behavior", routing_lines, inline=False)
    budget.add_line_fields("Shared visuals", _presentation_lines(settings), inline=False)


def _presentation_lines(settings: GuildSettings) -> tuple[str, ...]:
    return (
        f"Theme: {announcement_theme_label(settings.announcement_theme)}",
        f"Theme note: {announcement_theme_description(settings.announcement_theme)}",
        f"Title override: {settings.announcement_title_override or 'Default'}",
        f"Footer text: {settings.announcement_footer_text or 'Default'}",
        f"Image URL: {settings.announcement_image_url or 'None'}",
        f"Thumbnail URL: {settings.announcement_thumbnail_url or 'None'}",
        f"Accent color: {_format_accent_color(settings.announcement_accent_color)}",
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
            "Manage celebration copy, safe rich-media presentation, and preview flow without "
            "leaving Discord."
        ),
        "birthday": (
            "Birthday announcements use the shared theme plus the birthday-specific "
            "description template."
        ),
        "birthday_dm": (
            "Birthday DMs stay private and use a separate template from public announcements."
        ),
        "anniversary": (
            "Member anniversaries reuse the shared presentation but keep their own "
            "announcement copy and routing."
        ),
        "server_anniversary": (
            "Treat the server birthday as a first-class annual celebration with its own "
            "schedule and copy."
        ),
        "events": (
            "Custom annual events stay intentionally lightweight: yearly date-based "
            "celebrations with optional overrides."
        ),
        "help": (
            "Reference safe placeholders, media support, and preview behavior before editing "
            "templates."
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
        f"{celebration.name} | {month_name[celebration.event_month]} {celebration.event_day} | "
        f"{_format_enabled(celebration.enabled)} | {_format_channel(celebration.channel_id)}"
    )


def _effective_anniversary_channel(settings: GuildSettings) -> int | None:
    return settings.anniversary_channel_id or settings.announcement_channel_id


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

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        section: SectionName | None = None,
        note: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        latest = await self.settings_service.get_settings(interaction.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            interaction.guild,
            self.birthday_service,
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
            self.edit_secondary.label = "Edit visuals"
            self.reset_current.label = "Reset birthday copy"
        elif self.section == "birthday_dm":
            self.edit_primary.label = "Edit DM copy"
            self.edit_secondary.label = "Edit visuals"
            self.reset_current.label = "Reset DM copy"
        elif self.section == "anniversary":
            self.edit_primary.label = "Edit anniversary copy"
            self.edit_secondary.label = "Edit visuals"
            self.reset_current.label = "Reset anniversary copy"
        elif self.section == "server_anniversary":
            self.edit_primary.label = "Edit schedule"
            self.edit_secondary.label = "Edit event copy"
            self.reset_current.label = "Reset to guild date"
        elif self.section == "events":
            self.edit_primary.label = "Event commands"
            self.edit_secondary.label = "Edit visuals"
            self.reset_current.label = "Reset visuals"
            self.preview_current.disabled = len(self.recurring_events) == 0
        elif self.section == "help":
            self.edit_primary.disabled = True
            self.edit_secondary.disabled = True
            self.preview_current.disabled = True
            self.reset_current.disabled = True
        else:
            self.edit_primary.label = "Edit birthday copy"
            self.edit_secondary.label = "Edit visuals"
            self.preview_current.label = "Preview birthday"
            self.reset_current.label = "Reset visuals"

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=2)
    async def edit_primary(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if self.section == "server_anniversary":
            await interaction.response.send_modal(
                ServerAnniversaryConfigModal(
                    settings_service=self.settings_service,
                    birthday_service=self.birthday_service,
                    settings=self.settings,
                    owner_id=self.owner_id,
                    guild=self.guild,
                    celebration=self.server_anniversary,
                )
            )
            return
        if self.section == "events":
            await interaction.response.send_message(
                "Manage custom annual events with `/birthday event add`, "
                "`/birthday event edit`, and `/birthday event list`.",
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
                settings=self.settings,
                owner_id=self.owner_id,
                target=target,
                guild=self.guild,
                celebration=self.server_anniversary,
            )
        )

    @discord.ui.button(label="Edit visuals", style=discord.ButtonStyle.secondary, row=2)
    async def edit_secondary(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        if self.section == "server_anniversary":
            await interaction.response.send_modal(
                TemplateEditModal(
                    settings_service=self.settings_service,
                    birthday_service=self.birthday_service,
                    settings=self.settings,
                    owner_id=self.owner_id,
                    target="server_anniversary",
                    guild=self.guild,
                    celebration=self.server_anniversary,
                )
            )
            return
        await interaction.response.send_modal(
            StudioPresentationModal(
                settings_service=self.settings_service,
                settings=self.settings,
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
        try:
            status_embed, preview_embed = await _build_studio_preview_pair(
                guild=interaction.guild,
                settings=self.settings,
                settings_service=self.settings_service,
                section=self.section,
                server_anniversary=self.server_anniversary,
                recurring_events=self.recurring_events,
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
        try:
            note = await self._reset_section(interaction.guild)
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
                section=self.section,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
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

    async def _reset_section(self, guild: discord.Guild) -> str:
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
                enabled=self.server_anniversary.enabled if self.server_anniversary else False,
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
    image_url: discord.ui.TextInput[StudioPresentationModal] = discord.ui.TextInput(
        label="Image or GIF URL",
        required=False,
        max_length=500,
    )
    thumbnail_url: discord.ui.TextInput[StudioPresentationModal] = discord.ui.TextInput(
        label="Thumbnail URL",
        required=False,
        max_length=500,
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
        self.image_url.default = settings.announcement_image_url or ""
        self.thumbnail_url.default = settings.announcement_thumbnail_url or ""
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
                announcement_image_url=self.image_url.value,
                announcement_thumbnail_url=self.thumbnail_url.value,
                announcement_accent_color=self.accent_color.value,
            )
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
                "Shared title, footer, color, and media settings were updated.",
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


class ServerAnniversaryConfigModal(AdminPanelModal, title="Server anniversary"):
    enabled_input: discord.ui.TextInput[ServerAnniversaryConfigModal] = discord.ui.TextInput(
        label="Enabled (yes/no)",
        required=True,
        max_length=5,
        placeholder="yes",
    )
    date_source_input: discord.ui.TextInput[ServerAnniversaryConfigModal] = discord.ui.TextInput(
        label="Date source (guild/custom)",
        required=True,
        max_length=6,
        placeholder="guild",
    )
    month_input: discord.ui.TextInput[ServerAnniversaryConfigModal] = discord.ui.TextInput(
        label="Custom month",
        required=False,
        max_length=2,
        placeholder="3",
    )
    day_input: discord.ui.TextInput[ServerAnniversaryConfigModal] = discord.ui.TextInput(
        label="Custom day",
        required=False,
        max_length=2,
        placeholder="25",
    )
    channel_input: discord.ui.TextInput[ServerAnniversaryConfigModal] = discord.ui.TextInput(
        label="Channel override id",
        required=False,
        max_length=20,
        placeholder="Leave blank to use the main announcement channel",
    )

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        birthday_service: BirthdayService | None,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild | None,
        celebration: RecurringCelebration | None,
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.birthday_service = birthday_service
        self.settings = settings
        self.owner_id = owner_id
        state = _server_anniversary_state(guild=guild, celebration=celebration)
        self.enabled_input.default = "yes" if state.enabled else "no"
        self.date_source_input.default = "guild" if state.use_guild_created_date else "custom"
        self.month_input.default = (
            str(state.month) if state.month is not None and not state.use_guild_created_date else ""
        )
        self.day_input.default = (
            str(state.day) if state.day is not None and not state.use_guild_created_date else ""
        )
        self.channel_input.default = str(state.channel_id) if state.channel_id is not None else ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or self.birthday_service is None:
            await interaction.response.send_message(
                "This panel cannot edit the server anniversary here.",
                ephemeral=True,
            )
            return
        try:
            enabled = _parse_bool(self.enabled_input.value, label="Enabled")
            use_guild_created_date = _parse_date_source(self.date_source_input.value)
            override_month = _parse_optional_int(self.month_input.value, label="Month")
            override_day = _parse_optional_int(self.day_input.value, label="Day")
            channel_id = _parse_optional_int(self.channel_input.value, label="Channel override id")
            existing = await self.birthday_service.get_server_anniversary(interaction.guild.id)
            await self.birthday_service.upsert_server_anniversary(
                guild_id=interaction.guild.id,
                guild_created_at_utc=interaction.guild.created_at,
                override_month=override_month,
                override_day=override_day,
                channel_id=channel_id,
                template=existing.template if existing else None,
                enabled=enabled,
                use_guild_created_date=use_guild_created_date,
            )
        except (ValidationError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        server_anniversary, recurring_events = await _load_studio_context(
            interaction.guild,
            self.birthday_service,
        )
        await interaction.response.send_message(
            embed=_build_return_embed(
                "Server anniversary saved",
                "Server anniversary status, date source, and routing were updated.",
            ),
            view=StudioReturnView(
                settings_service=self.settings_service,
                birthday_service=self.birthday_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
                section="server_anniversary",
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
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
        readiness = await settings_service.describe_delivery(
            guild,
            kind="birthday_announcement",
        )
    elif section == "birthday_dm":
        preview = preview_context_for_kind("birthday_dm")
        preview_embed = build_announcement_message(
            kind="birthday_dm",
            server_name=guild.name,
            recipients=preview.recipients,
            celebration_mode=settings.celebration_mode,
            announcement_theme=settings.announcement_theme,
            presentation=settings.presentation(),
            template=settings.birthday_dm_template,
            preview_label="Preview only - birthday DM",
        ).embed
        readiness = await settings_service.describe_delivery(guild, kind="birthday_dm")
    elif section == "anniversary":
        preview = preview_context_for_kind("anniversary")
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
        readiness = await settings_service.describe_delivery(guild, kind="anniversary")
    elif section == "server_anniversary":
        state = _server_anniversary_state(guild=guild, celebration=server_anniversary)
        if state.month is None or state.day is None:
            raise ValidationError(
                "Discord did not provide the guild creation date. "
                "Save a custom server-anniversary date first."
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
        readiness = await settings_service.describe_delivery(
            guild,
            kind="server_anniversary",
            channel_id=state.channel_id,
        )
    else:
        if not recurring_events:
            raise ValidationError("Create a recurring annual event before previewing one here.")
        celebration = recurring_events[0]
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
        readiness = await settings_service.describe_delivery(
            guild,
            kind="recurring_event",
            channel_id=celebration.channel_id,
        )
    return _build_preview_status_embed(settings, readiness), preview_embed


def _build_preview_status_embed(
    settings: GuildSettings,
    readiness: object,
) -> discord.Embed:
    from bdayblaze.domain.models import AnnouncementDeliveryReadiness

    assert isinstance(readiness, AnnouncementDeliveryReadiness)
    budget = BudgetedEmbed.create(
        title="Dry-run preview",
        description="Preview only. No live celebration was sent.",
        color=discord.Color.green() if readiness.status == "ready" else discord.Color.orange(),
    )
    budget.add_field(name="Live delivery readiness", value=readiness.summary, inline=False)
    if readiness.details:
        budget.add_line_fields("Details", readiness.details, inline=False)
    budget.add_line_fields(
        "Current presentation",
        (
            f"Theme: {announcement_theme_label(settings.announcement_theme)}",
            f"Style: {settings.celebration_mode.title()}",
            f"Title override: {settings.announcement_title_override or 'Default'}",
            f"Image: {settings.announcement_image_url or 'None'}",
            f"Thumbnail: {settings.announcement_thumbnail_url or 'None'}",
        ),
        inline=False,
    )
    return budget.build()


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
        "birthday": "Public birthday announcement copy and visuals",
        "birthday_dm": "Private birthday DM copy",
        "anniversary": "Member anniversary setup",
        "server_anniversary": "Server birthday schedule and copy",
        "events": "Recurring annual event overview",
        "help": "Placeholders, media support, and reset notes",
    }
    return descriptions[section]


def _parse_bool(value: str, *, label: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"yes", "y", "true", "on", "enabled"}:
        return True
    if normalized in {"no", "n", "false", "off", "disabled"}:
        return False
    raise ValidationError(f"{label} must be yes or no.")


def _parse_date_source(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "guild":
        return True
    if normalized == "custom":
        return False
    raise ValidationError("Date source must be `guild` or `custom`.")


def _parse_optional_int(value: str, *, label: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValidationError(f"{label} must be a number.") from exc
