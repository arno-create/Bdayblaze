from __future__ import annotations

from typing import Final, Literal

import discord

from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNIVERSARY_TEMPLATE,
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    DEFAULT_DM_TEMPLATE,
    supported_placeholders,
)
from bdayblaze.domain.announcement_theme import (
    announcement_theme_description,
    announcement_theme_label,
    supported_announcement_themes,
)
from bdayblaze.domain.models import GuildSettings
from bdayblaze.domain.timezones import timezone_guidance
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.settings_service import SettingsService

_SETUP_TITLE: Final = "Birthday setup"
_MESSAGE_TITLE: Final = "Birthday Studio Lite"


def build_setup_embed(settings: GuildSettings, note: str | None = None) -> discord.Embed:
    channel_value = (
        f"<#{settings.announcement_channel_id}>"
        if settings.announcement_channel_id is not None
        else "Not set"
    )
    role_value = (
        f"<@&{settings.birthday_role_id}>" if settings.birthday_role_id is not None else "Not set"
    )
    eligibility_value = (
        f"<@&{settings.eligibility_role_id}>"
        if settings.eligibility_role_id is not None
        else "Everyone"
    )
    anniversary_channel = settings.anniversary_channel_id or settings.announcement_channel_id
    anniversary_value = (
        f"<#{anniversary_channel}>" if anniversary_channel is not None else "Not set"
    )
    embed = discord.Embed(
        title=_SETUP_TITLE,
        description=(
            "Control where celebrations go, which timezone drives server-level dates, and how "
            "operator safeguards behave."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Announcements",
        value=(
            f"Status: {'Enabled' if settings.announcements_enabled else 'Disabled'}\n"
            f"Birthday channel: {channel_value}\n"
            f"Theme: {announcement_theme_label(settings.announcement_theme)}\n"
            f"Style: {settings.celebration_mode.title()}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Eligibility and anti-spam",
        value=(
            f"Eligibility role: {eligibility_value}\n"
            f"Ignore bots: {'Yes' if settings.ignore_bots else 'No'}\n"
            f"Minimum membership age: {settings.minimum_membership_days} day(s)\n"
            f"Mention suppression threshold: {settings.mention_suppression_threshold}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Roles and DMs",
        value=(
            f"Birthday role: {'Enabled' if settings.role_enabled else 'Disabled'} ({role_value})\n"
            f"Birthday DM: {'Enabled' if settings.birthday_dm_enabled else 'Disabled'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Anniversaries",
        value=(
            f"Status: {'Enabled' if settings.anniversary_enabled else 'Disabled'}\n"
            f"Channel: {anniversary_value}\n"
            "Tracked-only model. Sync from stored birthdays or a selected member."
        ),
        inline=False,
    )
    embed.add_field(
        name="Default timezone",
        value=(
            f"Saved: `{settings.default_timezone}`\n"
            f"Examples: {timezone_guidance(allow_server_default=False)}"
        ),
        inline=False,
    )
    if note:
        embed.add_field(name="Saved", value=note, inline=False)
    return embed


def build_timezone_help_embed(*, allow_server_default: bool) -> discord.Embed:
    embed = discord.Embed(
        title="Timezone help",
        description=timezone_guidance(allow_server_default=allow_server_default),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What to enter",
        value=(
            "Use the full IANA timezone name.\n"
            "Good examples: `Asia/Yerevan`, `Europe/London`, `Europe/Berlin`, "
            "`America/New_York`, `America/Los_Angeles`, `Asia/Tokyo`."
        ),
        inline=False,
    )
    return embed


def build_message_template_embed(
    settings: GuildSettings,
    *,
    note: str | None = None,
) -> discord.Embed:
    presentation = settings.presentation()
    embed = discord.Embed(
        title=_MESSAGE_TITLE,
        description=(
            "Customize safe templates and Studio Lite presentation. Reliable pings still come "
            "from message content, so embed text stays presentation-focused."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current presentation",
        value=(
            f"Theme: {announcement_theme_label(settings.announcement_theme)}\n"
            f"{announcement_theme_description(settings.announcement_theme)}\n"
            f"Title override: {presentation.title_override or 'Default'}\n"
            f"Footer text: {presentation.footer_text or 'Default'}\n"
            f"Image URL: {presentation.image_url or 'None'}\n"
            f"Thumbnail URL: {presentation.thumbnail_url or 'None'}\n"
            "Accent color: "
            f"{_format_accent_color(presentation.accent_color)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Birthday announcement template",
        value=_code_block(settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE),
        inline=False,
    )
    embed.add_field(
        name="Birthday DM template",
        value=_code_block(settings.birthday_dm_template or DEFAULT_DM_TEMPLATE),
        inline=False,
    )
    embed.add_field(
        name="Anniversary template",
        value=_code_block(settings.anniversary_template or DEFAULT_ANNIVERSARY_TEMPLATE),
        inline=False,
    )
    placeholder_lines = [
        f"`{{{placeholder}}}` - {description}"
        for placeholder, description in supported_placeholders()
    ]
    embed.add_field(name="Safe placeholders", value="\n".join(placeholder_lines), inline=False)
    if note:
        embed.add_field(name="Saved", value=note, inline=False)
    return embed


class SetupView(discord.ui.View):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
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
            )
        )

    @discord.ui.button(label="Message setup", style=discord.ButtonStyle.secondary, row=4)
    async def open_message_setup(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        await interaction.response.edit_message(
            embed=build_message_template_embed(self.settings),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
                guild=interaction.guild,
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
            placeholder="Select dedicated birthday role", min_values=0, max_values=1, row=1
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
            placeholder="Select optional eligibility role", min_values=0, max_values=1, row=2
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


class MessageTemplateView(discord.ui.View):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
        guild: discord.Guild | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.guild = guild
        self.add_item(AnnouncementThemeSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This message panel belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Edit birthday template", style=discord.ButtonStyle.primary, row=1)
    async def edit_birthday_template(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        await interaction.response.send_modal(
            TemplateEditModal(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
                target="birthday",
            )
        )

    @discord.ui.button(label="Edit DM template", style=discord.ButtonStyle.secondary, row=1)
    async def edit_dm_template(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        await interaction.response.send_modal(
            TemplateEditModal(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
                target="birthday_dm",
            )
        )

    @discord.ui.button(
        label="Edit anniversary template", style=discord.ButtonStyle.secondary, row=1
    )
    async def edit_anniversary_template(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        await interaction.response.send_modal(
            TemplateEditModal(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
                target="anniversary",
            )
        )

    @discord.ui.button(label="Studio settings", style=discord.ButtonStyle.secondary, row=1)
    async def edit_presentation(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        await interaction.response.send_modal(
            StudioPresentationModal(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
            )
        )

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.secondary, row=2)
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
            ),
        )


class AnnouncementThemeSelect(discord.ui.Select["MessageTemplateView"]):
    def __init__(self, message_view: MessageTemplateView) -> None:
        options = [
            discord.SelectOption(
                label=spec.label,
                value=spec.key,
                description=spec.description,
                default=spec.key == message_view.settings.announcement_theme,
            )
            for spec in supported_announcement_themes()
        ]
        super().__init__(
            placeholder=(
                f"Theme: {announcement_theme_label(message_view.settings.announcement_theme)}"
            ),
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.message_view = message_view

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.message_view.settings_service.update_settings(
            interaction.guild,
            announcement_theme=self.values[0],  # type: ignore[arg-type]
        )
        latest = await self.message_view.settings_service.get_settings(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_message_template_embed(latest, note="Announcement theme saved."),
            view=MessageTemplateView(
                settings_service=self.message_view.settings_service,
                settings=latest,
                owner_id=self.message_view.owner_id,
                guild=interaction.guild,
            ),
        )


class TimezoneModal(discord.ui.Modal, title="Set default timezone"):
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
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.timezone.default = current_timezone

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
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
            embed=build_setup_embed(
                latest, note=f"Default timezone saved as `{latest.default_timezone}`."
            ),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )


class MembershipRulesModal(discord.ui.Modal, title="Membership and anti-spam rules"):
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
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.minimum_days.default = str(settings.minimum_membership_days)
        self.mention_threshold.default = str(settings.mention_suppression_threshold)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
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
            embed=build_setup_embed(latest, note="Membership and anti-spam rules saved."),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )


class TemplateEditModal(discord.ui.Modal):
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
        settings: GuildSettings,
        owner_id: int,
        target: Literal["birthday", "birthday_dm", "anniversary"],
    ) -> None:
        super().__init__(title=f"Edit {target.replace('_', ' ')} template")
        self.settings_service = settings_service
        self.owner_id = owner_id
        self.target = target
        if target == "birthday":
            self.template_input.default = (
                settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE
            )
        elif target == "birthday_dm":
            self.template_input.default = settings.birthday_dm_template or DEFAULT_DM_TEMPLATE
        else:
            self.template_input.default = (
                settings.anniversary_template or DEFAULT_ANNIVERSARY_TEMPLATE
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
            )
            return
        value = self.template_input.value.strip() or None
        try:
            if self.target == "birthday":
                await self.settings_service.update_settings(
                    interaction.guild,
                    announcement_template=value,
                )
            elif self.target == "birthday_dm":
                await self.settings_service.update_settings(
                    interaction.guild,
                    birthday_dm_template=value,
                )
            else:
                await self.settings_service.update_settings(
                    interaction.guild,
                    anniversary_template=value,
                )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_message_template_embed(
                latest, note=f"{self.target.replace('_', ' ').title()} template saved."
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )


class StudioPresentationModal(discord.ui.Modal, title="Birthday Studio Lite"):
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
    ) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.owner_id = owner_id
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
                "This can only be used in a server.", ephemeral=True
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
        await interaction.response.send_message(
            embed=build_message_template_embed(latest, note="Studio Lite settings saved."),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )


def _code_block(value: str) -> str:
    return f"```text\n{value}\n```"


def _format_accent_color(value: int | None) -> str:
    if value is None:
        return "Preset"
    return f"#{value:06X}"
