from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.discord.ui.setup import (
    MessageTemplateView,
    SetupView,
    build_message_template_embed,
    build_setup_embed,
)
from bdayblaze.domain.models import BirthdayPreview
from bdayblaze.domain.timezones import autocomplete_timezones
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import NotFoundError, ValidationError
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.settings_service import SettingsService


class BirthdayGroup(
    commands.GroupCog,
    group_name="birthday",
    group_description="Manage your birthday and server birthday settings",
):
    def __init__(
        self,
        birthday_service: BirthdayService,
        settings_service: SettingsService,
        health_service: HealthService,
    ) -> None:
        super().__init__()
        self._birthday_service = birthday_service
        self._settings_service = settings_service
        self._health_service = health_service

    @app_commands.command(name="set", description="Save or update your birthday for this server.")
    @app_commands.describe(
        month="Birth month as a number",
        day="Birth day as a number",
        year="Optional birth year. Leave this out to keep it private.",
        timezone=(
            "Optional IANA timezone like Asia/Yerevan. Leave this blank to use the server default."
        ),
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
        embed.add_field(
            name="Date",
            value=f"{birthday.birth_month:02d}/{birthday.birth_day:02d}",
            inline=True,
        )
        embed.add_field(name="Timezone", value=effective_timezone, inline=True)
        embed.add_field(
            name="Birth year",
            value=str(birthday.birth_year) if birthday.birth_year is not None else "Not stored",
            inline=True,
        )
        embed.add_field(
            name="Next celebration",
            value=discord.utils.format_dt(birthday.next_occurrence_at_utc, "F"),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @set_birthday.autocomplete("timezone")
    async def birthday_timezone_autocomplete(
        self,
        _: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=timezone_name, value=timezone_name)
            for timezone_name in autocomplete_timezones(current)
        ]

    @app_commands.command(name="view", description="View your saved birthday for this server.")
    @app_commands.guild_only()
    async def view_birthday(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            birthday = await self._birthday_service.get_birthday(
                interaction.guild.id,
                interaction.user.id,
            )
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        settings = await self._settings_service.get_settings(interaction.guild.id)
        effective_timezone = birthday.effective_timezone(settings)
        embed = discord.Embed(
            title="Your birthday settings",
            description="This record stays scoped to the current server.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Date",
            value=f"{birthday.birth_month:02d}/{birthday.birth_day:02d}",
            inline=True,
        )
        embed.add_field(name="Timezone", value=effective_timezone, inline=True)
        embed.add_field(
            name="Birth year",
            value=str(birthday.birth_year) if birthday.birth_year is not None else "Not stored",
            inline=True,
        )
        embed.add_field(
            name="Next celebration",
            value=discord.utils.format_dt(birthday.next_occurrence_at_utc, "F"),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Delete your saved birthday for this server.")
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Delete your birthday data for this server?",
            ephemeral=True,
            view=ConfirmBirthdayDeletionView(self._birthday_service, interaction.user.id),
        )

    @app_commands.command(name="upcoming", description="See the next birthdays in this server.")
    @app_commands.describe(limit="How many upcoming birthdays to show")
    @app_commands.guild_only()
    async def upcoming_birthdays(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        fetch_limit = min(max(limit * 3, limit), 50)
        upcoming = await self._birthday_service.list_upcoming_birthdays(
            interaction.guild.id,
            fetch_limit,
        )
        resolved = await _resolve_upcoming_members(interaction.guild, upcoming)
        lines = [
            (
                f"{member.mention} - {preview.birth_month:02d}/{preview.birth_day:02d} - "
                f"{discord.utils.format_dt(preview.next_occurrence_at_utc, 'R')}"
            )
            for preview, member in resolved[:limit]
        ]
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
        if len(resolved) < min(limit, len(upcoming)):
            embed.set_footer(text="Some members could not be resolved and were skipped.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="setup", description="Open the server birthday setup panel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_setup_embed(settings),
            view=SetupView(
                settings_service=self._settings_service,
                settings=settings,
                owner_id=interaction.user.id,
                guild=interaction.guild,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="message", description="Open the birthday message setup panel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def message(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_message_template_embed(settings),
            view=MessageTemplateView(
                settings_service=self._settings_service,
                settings=settings,
                owner_id=interaction.user.id,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="health", description="Run birthday setup and scheduler health checks."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def health(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        issues = await self._health_service.inspect_guild(interaction.guild)
        if not issues:
            embed = discord.Embed(
                title="Health check",
                description="No actionable issues were detected.",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        lines = [
            f"[{issue.severity.upper()}] `{issue.code}`: {issue.summary}\nAction: {issue.action}"
            for issue in issues
        ]
        embed = discord.Embed(
            title="Health check",
            description="\n\n".join(lines),
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="privacy", description="Explain what birthday data is stored and where."
    )
    @app_commands.guild_only()
    async def privacy(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Privacy",
            description=(
                "Bdayblaze stores birthdays per server membership, not across servers. "
                "Only month/day, an optional birth year, and an optional timezone override "
                "are stored."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Scope",
            value=(
                "Birthdays stay scoped to this server. Cross-server public birthday sharing is not "
                "enabled in this version."
            ),
            inline=False,
        )
        embed.add_field(
            name="Deletion",
            value="Use `/birthday remove` to delete your birthday data for this server.",
            inline=False,
        )
        embed.add_field(
            name="Operations",
            value=(
                "Logs avoid raw birth dates, birth years, and message-template content "
                "with personal data."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ConfirmBirthdayDeletionView(discord.ui.View):
    def __init__(self, birthday_service: BirthdayService, owner_id: int) -> None:
        super().__init__(timeout=300)
        self._birthday_service = birthday_service
        self._owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message(
                "This delete prompt is not yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Delete birthday data", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ConfirmBirthdayDeletionView],
    ) -> None:
        assert interaction.guild is not None
        try:
            deleted = await self._birthday_service.remove_birthday(
                interaction.guild.id,
                interaction.user.id,
            )
        except NotFoundError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if deleted.active_birthday_role_id is not None and isinstance(
            interaction.user, discord.Member
        ):
            role = interaction.guild.get_role(deleted.active_birthday_role_id)
            if role is not None and role in interaction.user.roles:
                try:
                    await interaction.user.remove_roles(
                        role,
                        reason="Bdayblaze user deleted birthday data",
                    )
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
        _: discord.ui.Button[ConfirmBirthdayDeletionView],
    ) -> None:
        await interaction.response.edit_message(
            content="Deletion cancelled.", embed=None, view=None
        )


async def _resolve_upcoming_members(
    guild: discord.Guild,
    previews: list[BirthdayPreview],
) -> list[tuple[BirthdayPreview, discord.Member]]:
    resolved: list[tuple[BirthdayPreview, discord.Member]] = []
    unresolved: list[BirthdayPreview] = []
    for preview in previews:
        member = guild.get_member(preview.user_id)
        if member is not None:
            resolved.append((preview, member))
            continue
        unresolved.append(preview)

    if not unresolved:
        return resolved

    semaphore = asyncio.Semaphore(4)

    async def fetch_preview_member(
        preview: BirthdayPreview,
    ) -> tuple[BirthdayPreview, discord.Member | None]:
        async with semaphore:
            try:
                member = await guild.fetch_member(preview.user_id)
            except (discord.NotFound, discord.HTTPException):
                member = None
        return preview, member

    fetched = await asyncio.gather(*(fetch_preview_member(preview) for preview in unresolved))
    resolved.extend((preview, member) for preview, member in fetched if member is not None)
    return resolved
