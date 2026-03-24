from __future__ import annotations

import discord

from bdayblaze.domain.models import GuildSettings
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.settings_service import SettingsService


def build_settings_embed(settings: GuildSettings, note: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="Bdayblaze setup",
        description=(
            "Configure the birthday channel, default timezone, and optional dedicated birthday role. "
            "Admin responses stay ephemeral by default."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Announcement channel",
        value=f"<#{settings.announcement_channel_id}>" if settings.announcement_channel_id else "Not set",
        inline=False,
    )
    embed.add_field(name="Default timezone", value=settings.default_timezone, inline=True)
    embed.add_field(
        name="Announcements",
        value="Enabled" if settings.announcements_enabled else "Disabled",
        inline=True,
    )
    embed.add_field(
        name="Role assignment",
        value="Enabled" if settings.role_enabled else "Disabled",
        inline=True,
    )
    embed.add_field(
        name="Birthday role",
        value=f"<@&{settings.birthday_role_id}>" if settings.birthday_role_id else "Not set",
        inline=False,
    )
    embed.add_field(name="Celebration mode", value=settings.celebration_mode.title(), inline=True)
    embed.add_field(
        name="Role policy",
        value="If enabled, use a dedicated role that the bot is allowed to manage end-to-end.",
        inline=False,
    )
    if note:
        embed.add_field(name="Update", value=note, inline=False)
    return embed


class SetupView(discord.ui.View):
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

    async def refresh(self, interaction: discord.Interaction, note: str | None = None) -> None:
        assert interaction.guild is not None
        latest = await self.settings_service.get_settings(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_settings_embed(latest, note),
            view=SetupView(
                settings_service=self.settings_service,
                settings=latest,
                owner_id=self.owner_id,
            ),
        )

    @discord.ui.button(label="Set timezone", style=discord.ButtonStyle.primary, row=2)
    async def set_timezone(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            TimezoneModal(
                settings_service=self.settings_service,
                current_timezone=self.settings.default_timezone,
            )
        )

    @discord.ui.button(label="Toggle announcements", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_announcements(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
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
        await self.refresh(
            interaction,
            note=f"Announcements {'enabled' if not self.settings.announcements_enabled else 'disabled'}.",
        )

    @discord.ui.button(label="Toggle role assignment", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_role_assignment(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
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
        await self.refresh(
            interaction,
            note=f"Role assignment {'enabled' if not self.settings.role_enabled else 'disabled'}.",
        )

    @discord.ui.button(label="Toggle mode", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_mode(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        next_mode = "party" if self.settings.celebration_mode == "quiet" else "quiet"
        await self.settings_service.update_settings(interaction.guild, celebration_mode=next_mode)
        await self.refresh(interaction, note=f"Celebration mode changed to {next_mode}.")

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.refresh(interaction, note="Configuration reloaded.")


class AnnouncementChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, setup_view: SetupView) -> None:
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Select announcement channel",
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


class BirthdayRoleSelect(discord.ui.RoleSelect):
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


class TimezoneModal(discord.ui.Modal, title="Set default timezone"):
    timezone = discord.ui.TextInput(
        label="IANA timezone",
        placeholder="Europe/Berlin",
        required=True,
        max_length=64,
    )

    def __init__(self, *, settings_service: SettingsService, current_timezone: str) -> None:
        super().__init__()
        self.settings_service = settings_service
        self.timezone.default = current_timezone

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        try:
            await self.settings_service.update_settings(
                interaction.guild,
                default_timezone=self.timezone.value,
            )
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Default timezone updated to `{self.timezone.value}`. Use Refresh on the setup panel to reload it.",
            ephemeral=True,
        )
