from __future__ import annotations

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.domain.birthday_logic import compute_age, occurrence_local_date
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import NotFoundError, ValidationError
from bdayblaze.services.settings_service import SettingsService


class BirthdayGroup(commands.GroupCog, group_name="birthday", group_description="Manage your birthday"):
    def __init__(
        self,
        birthday_service: BirthdayService,
        settings_service: SettingsService,
    ) -> None:
        super().__init__()
        self._birthday_service = birthday_service
        self._settings_service = settings_service

    @app_commands.command(name="set", description="Register or update your birthday for this server.")
    @app_commands.describe(
        month="Birth month as a number",
        day="Birth day as a number",
        year="Optional birth year",
        timezone="Optional IANA timezone override, for example Europe/Berlin",
    )
    @app_commands.guild_only()
    async def set_birthday(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12],
        day: app_commands.Range[int, 1, 31],
        year: app_commands.Range[int, 1900, 9999] | None = None,
        timezone: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            birthday = await self._birthday_service.set_birthday(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                month=month,
                day=day,
                birth_year=year,
                timezone_override=timezone,
            )
        except ValidationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        settings = await self._settings_service.get_settings(interaction.guild.id)
        effective_timezone = birthday.effective_timezone(settings)
        embed = discord.Embed(
            title="Birthday saved",
            description="Your birthday is stored only for this server.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Date", value=f"{birthday.birth_month:02d}/{birthday.birth_day:02d}", inline=True)
        embed.add_field(name="Timezone", value=effective_timezone, inline=True)
        embed.add_field(
            name="Birth year",
            value=str(birthday.birth_year) if birthday.birth_year is not None else "Not stored",
            inline=True,
        )
        embed.add_field(name="Age visibility", value="Hidden by default", inline=True)
        embed.add_field(
            name="Next celebration",
            value=discord.utils.format_dt(birthday.next_occurrence_at_utc, "F"),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="view", description="View your stored birthday settings in this server.")
    @app_commands.guild_only()
    async def view_birthday(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            birthday = await self._birthday_service.get_birthday(interaction.guild.id, interaction.user.id)
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        settings = await self._settings_service.get_settings(interaction.guild.id)
        effective_timezone = birthday.effective_timezone(settings)
        local_date = occurrence_local_date(birthday.next_occurrence_at_utc, effective_timezone)
        embed = discord.Embed(
            title="Your birthday settings",
            description="Bdayblaze keeps this record scoped to the current server.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Date", value=f"{birthday.birth_month:02d}/{birthday.birth_day:02d}", inline=True)
        embed.add_field(name="Timezone", value=effective_timezone, inline=True)
        embed.add_field(
            name="Birth year",
            value=str(birthday.birth_year) if birthday.birth_year is not None else "Not stored",
            inline=True,
        )
        age = compute_age(birthday.birth_year, local_date)
        embed.add_field(name="Age visibility", value="Hidden by default", inline=True)
        embed.add_field(name="Computed age", value=str(age) if age is not None else "Not stored", inline=True)
        embed.add_field(
            name="Next celebration",
            value=discord.utils.format_dt(birthday.next_occurrence_at_utc, "F"),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Delete your stored birthday data for this server.")
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Delete your birthday data for this server?",
            ephemeral=True,
            view=ConfirmBirthdayDeletionView(self._birthday_service, interaction.user.id),
        )

    @app_commands.command(name="upcoming", description="See the next birthdays coming up in this server.")
    @app_commands.describe(limit="How many upcoming birthdays to show")
    @app_commands.guild_only()
    async def upcoming_birthdays(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        upcoming = await self._birthday_service.list_upcoming_birthdays(interaction.guild.id, limit)
        lines: list[str] = []
        for preview in upcoming:
            member = interaction.guild.get_member(preview.user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(preview.user_id)
                except (discord.NotFound, discord.HTTPException):
                    continue
            lines.append(
                f"{member.mention} - {preview.birth_month:02d}/{preview.birth_day:02d} - "
                f"{discord.utils.format_dt(preview.next_occurrence_at_utc, 'R')}"
            )
        if not lines:
            await interaction.followup.send(
                "No upcoming birthdays are registered in this server yet.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Upcoming birthdays",
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=datetime.now(UTC),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class ConfirmBirthdayDeletionView(discord.ui.View):
    def __init__(self, birthday_service: BirthdayService, owner_id: int) -> None:
        super().__init__(timeout=300)
        self._birthday_service = birthday_service
        self._owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message("This delete prompt is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Delete birthday data", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        try:
            deleted = await self._birthday_service.remove_birthday(interaction.guild.id, interaction.user.id)
        except NotFoundError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if deleted.active_birthday_role_id is not None and isinstance(interaction.user, discord.Member):
            role = interaction.guild.get_role(deleted.active_birthday_role_id)
            if role is not None and role in interaction.user.roles:
                try:
                    await interaction.user.remove_roles(role, reason="Bdayblaze user deleted birthday data")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await interaction.response.edit_message(
            content="Your birthday data for this server has been deleted.",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(content="Deletion cancelled.", embed=None, view=None)
