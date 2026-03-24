from __future__ import annotations

from typing import Final

import discord

from bdayblaze.discord.announcements import (
    build_announcement_message,
    preview_batch_recipients,
    preview_single_recipients,
)
from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    celebration_mode_label,
    supported_placeholders,
)
from bdayblaze.domain.announcement_theme import (
    announcement_theme_description,
    announcement_theme_label,
    supported_announcement_themes,
)
from bdayblaze.domain.models import CelebrationMode, GuildSettings
from bdayblaze.domain.timezones import timezone_guidance
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.settings_service import SettingsService

_SETUP_TITLE: Final = "Birthday setup"
_MESSAGE_TITLE: Final = "Birthday message"


def build_setup_embed(settings: GuildSettings, note: str | None = None) -> discord.Embed:
    channel_value = (
        f"<#{settings.announcement_channel_id}>"
        if settings.announcement_channel_id is not None
        else "Not set"
    )
    role_value = (
        f"<@&{settings.birthday_role_id}>" if settings.birthday_role_id is not None else "Not set"
    )
    embed = discord.Embed(
        title=_SETUP_TITLE,
        description=(
            "Control where birthday posts go, which timezone is used by default, "
            "and whether the bot manages a dedicated birthday role."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Announcements",
        value=(
            f"Status: {'Enabled' if settings.announcements_enabled else 'Disabled'}\n"
            f"Channel: {channel_value}\n"
            f"Style: {celebration_mode_label(settings.celebration_mode)}\n"
            f"Theme: {announcement_theme_label(settings.announcement_theme)}"
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
    embed.add_field(
        name="Birthday role",
        value=(
            f"Status: {'Enabled' if settings.role_enabled else 'Disabled'}\n"
            f"Role: {role_value}\n"
            "Rule: Pick a dedicated role the bot can manage, not a managed role or @everyone."
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
    if allow_server_default:
        embed.add_field(
            name="Server default",
            value="Leave the field blank to use this server's saved default timezone.",
            inline=False,
        )
    return embed


def build_message_template_embed(
    settings: GuildSettings,
    *,
    note: str | None = None,
) -> discord.Embed:
    current_template = settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE
    embed = discord.Embed(
        title=_MESSAGE_TITLE,
        description=(
            "Customize the embed text used for birthday announcements. "
            "Mentions are still sent separately so notification behavior stays reliable."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current presentation",
        value=(
            f"Mode: {celebration_mode_label(settings.celebration_mode)}\n"
            f"Theme: {announcement_theme_label(settings.announcement_theme)}\n"
            f"{announcement_theme_description(settings.announcement_theme)}"
        ),
        inline=False,
    )
    embed.add_field(name="Current template", value=_code_block(current_template), inline=False)
    embed.add_field(
        name="Default template",
        value=_code_block(DEFAULT_ANNOUNCEMENT_TEMPLATE),
        inline=False,
    )
    embed.add_field(
        name="How previews work",
        value=(
            "Preview examples use safe sample members. Single-value date placeholders use the "
            "shared date when the whole batch matches, otherwise they fall back to generic "
            "multi-birthday text."
        ),
        inline=False,
    )
    embed.add_field(
        name="Template tips",
        value="Use `{{` and `}}` for literal braces in your message.",
        inline=False,
    )
    person_lines = [
        f"`{{{placeholder}}}` - {description}"
        for placeholder, description in supported_placeholders()
        if placeholder in {"user.mention", "user.display_name", "user.name", "server.name"}
    ]
    batch_lines = [
        f"`{{{placeholder}}}` - {description}"
        for placeholder, description in supported_placeholders()
        if placeholder not in {"user.mention", "user.display_name", "user.name", "server.name"}
    ]
    embed.add_field(
        name="Person and server placeholders",
        value="\n".join(person_lines),
        inline=False,
    )
    embed.add_field(
        name="Batch and timing placeholders",
        value="\n".join(batch_lines),
        inline=False,
    )
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
        self.toggle_announcements.label = (
            "Disable announcements" if settings.announcements_enabled else "Enable announcements"
        )
        self.toggle_role_assignment.label = (
            "Disable birthday role" if settings.role_enabled else "Enable birthday role"
        )
        self.toggle_style.label = (
            "Use quiet style" if settings.celebration_mode == "party" else "Use festive style"
        )
        self.add_item(AnnouncementChannelSelect(self))
        self.add_item(BirthdayRoleSelect(self))

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

    @discord.ui.button(label="Set timezone", style=discord.ButtonStyle.primary, row=2)
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

    @discord.ui.button(label="Timezone help", style=discord.ButtonStyle.secondary, row=2)
    async def timezone_help(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        await interaction.response.send_message(
            embed=build_timezone_help_embed(allow_server_default=False),
            ephemeral=True,
        )

    @discord.ui.button(label="Enable announcements", style=discord.ButtonStyle.secondary, row=2)
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
        state = "enabled" if not self.settings.announcements_enabled else "disabled"
        await self.refresh(interaction, note=f"Announcements {state}.")

    @discord.ui.button(label="Enable birthday role", style=discord.ButtonStyle.secondary, row=2)
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
        state = "enabled" if not self.settings.role_enabled else "disabled"
        await self.refresh(interaction, note=f"Birthday role assignment {state}.")

    @discord.ui.button(label="Use festive style", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_style(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        assert interaction.guild is not None
        next_mode: CelebrationMode = (
            "party" if self.settings.celebration_mode == "quiet" else "quiet"
        )
        await self.settings_service.update_settings(
            interaction.guild,
            celebration_mode=next_mode,
        )
        await self.refresh(
            interaction,
            note=f"Announcement style saved as {celebration_mode_label(next_mode)}.",
        )

    @discord.ui.button(label="Message setup", style=discord.ButtonStyle.secondary, row=3)
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
            ),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=3)
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[SetupView],
    ) -> None:
        await self.refresh(interaction, note="Setup reloaded.")


class AnnouncementChannelSelect(discord.ui.ChannelSelect["SetupView"]):
    def __init__(self, setup_view: SetupView) -> None:
        channel_name = None
        if setup_view.guild is not None and setup_view.settings.announcement_channel_id is not None:
            channel = setup_view.guild.get_channel(setup_view.settings.announcement_channel_id)
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else None
        placeholder = (
            f"Saved channel: #{channel_name or setup_view.settings.announcement_channel_id}"
            if setup_view.settings.announcement_channel_id is not None
            else "Select announcement channel"
        )
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder=placeholder,
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
        note = (
            "Announcement channel cleared." if channel_id is None else "Announcement channel saved."
        )
        await self.setup_view.refresh(interaction, note=note)


class BirthdayRoleSelect(discord.ui.RoleSelect["SetupView"]):
    def __init__(self, setup_view: SetupView) -> None:
        role_name = None
        if setup_view.guild is not None and setup_view.settings.birthday_role_id is not None:
            role = setup_view.guild.get_role(setup_view.settings.birthday_role_id)
            role_name = role.name if role is not None else None
        placeholder = (
            f"Saved role: @{role_name or setup_view.settings.birthday_role_id}"
            if setup_view.settings.birthday_role_id is not None
            else "Select dedicated birthday role"
        )
        super().__init__(
            placeholder=placeholder,
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
        note = "Birthday role cleared." if role_id is None else "Birthday role saved."
        await self.setup_view.refresh(interaction, note=note)


class MessageTemplateView(discord.ui.View):
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        settings: GuildSettings,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=900)
        self.settings_service = settings_service
        self.settings = settings
        self.owner_id = owner_id
        self.add_item(AnnouncementThemeSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This message panel belongs to a different admin.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Edit template", style=discord.ButtonStyle.primary, row=0)
    async def edit_template(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        await interaction.response.send_modal(
            TemplateEditModal(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
            )
        )

    @discord.ui.button(label="Preview examples", style=discord.ButtonStyle.secondary, row=0)
    async def preview_examples(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        single_preview = build_announcement_message(
            server_name="Bdayblaze HQ",
            recipients=preview_single_recipients(),
            celebration_mode=self.settings.celebration_mode,
            announcement_theme=self.settings.announcement_theme,
            template=self.settings.announcement_template,
            preview_label="Preview only - single birthday example",
        )
        batch_preview = build_announcement_message(
            server_name="Bdayblaze HQ",
            recipients=preview_batch_recipients(),
            celebration_mode=self.settings.celebration_mode,
            announcement_theme=self.settings.announcement_theme,
            template=self.settings.announcement_template,
            preview_label="Preview only - multi-birthday example",
        )
        await interaction.response.edit_message(
            embeds=[
                build_message_template_embed(
                    self.settings,
                    note="Preview examples refreshed below.",
                ),
                single_preview.embed,
                batch_preview.embed,
            ],
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=self.settings,
                owner_id=self.owner_id,
            ),
        )

    @discord.ui.button(label="Reset to default", style=discord.ButtonStyle.secondary, row=0)
    async def reset_template(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[MessageTemplateView],
    ) -> None:
        assert interaction.guild is not None
        await self.settings_service.update_settings(
            interaction.guild,
            announcement_template=None,
        )
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                note="Announcement message reset to the default template.",
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
            ),
        )

    @discord.ui.button(label="Back to setup", style=discord.ButtonStyle.secondary, row=0)
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
        try:
            await self.message_view.settings_service.update_settings(
                interaction.guild,
                announcement_theme=self.values[0],  # type: ignore[arg-type]
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.message_view.settings_service.get_settings(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_message_template_embed(
                latest,
                note=(
                    "Announcement theme saved as "
                    f"{announcement_theme_label(latest.announcement_theme)}."
                ),
            ),
            view=MessageTemplateView(
                settings_service=self.message_view.settings_service,
                settings=latest,
                owner_id=self.message_view.owner_id,
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
                latest,
                note=f"Default timezone saved as `{latest.default_timezone}`.",
            ),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )


class TemplateEditModal(discord.ui.Modal, title="Edit birthday message"):
    template_input: discord.ui.TextInput[TemplateEditModal] = discord.ui.TextInput(
        label="Announcement body",
        placeholder="Use placeholders like {birthday.mentions}. Escape braces with {{ and }}.",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
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
        self.settings = settings
        self.owner_id = owner_id
        self.template_input.default = (
            settings.announcement_template or DEFAULT_ANNOUNCEMENT_TEMPLATE
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
            )
            return
        template_value = self.template_input.value.strip() or None
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                announcement_template=template_value,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_message_template_embed(
                latest,
                note=(
                    "Custom announcement message saved."
                    if latest.announcement_template is not None
                    else "Announcement message reset to the default template."
                ),
            ),
            view=MessageTemplateView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
            ),
            ephemeral=True,
        )


def _code_block(value: str) -> str:
    return f"```text\n{value}\n```"
