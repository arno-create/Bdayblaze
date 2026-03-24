from __future__ import annotations

from calendar import month_name
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.discord.announcements import (
    build_announcement_message,
    preview_batch_recipients,
    preview_single_recipients,
)
from bdayblaze.discord.member_resolution import resolve_guild_members
from bdayblaze.discord.ui.info import build_about_embed, build_help_embed
from bdayblaze.discord.ui.setup import (
    MessageTemplateView,
    SetupView,
    build_message_template_embed,
    build_setup_embed,
)
from bdayblaze.domain.announcement_template import celebration_mode_label
from bdayblaze.domain.announcement_theme import announcement_theme_label
from bdayblaze.domain.birthday_logic import current_celebration_window_utc
from bdayblaze.domain.models import BirthdayPreview, GuildSettings, MemberBirthday
from bdayblaze.domain.timezones import autocomplete_timezones
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import NotFoundError, ValidationError
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.settings_service import SettingsService

_PUBLIC_RESULT_LIMIT = 12
_ADMIN_RESULT_LIMIT = 15


class BirthdayGroup(
    commands.GroupCog,
    group_name="birthday",
    group_description="Manage your birthday and server birthday settings",
):
    member = app_commands.Group(
        name="member",
        description="Privately manage another member's birthday record.",
    )

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

    @app_commands.command(
        name="help",
        description="Show the main Bdayblaze commands and setup flow.",
    )
    async def help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)

    @app_commands.command(
        name="about",
        description="Explain what Bdayblaze stores, how it works, and where to get help.",
    )
    async def about(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=build_about_embed(), ephemeral=True)

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
        await interaction.followup.send(
            embed=_build_birthday_embed(
                title="Birthday saved",
                description="Your birthday is stored only for this server.",
                birthday=birthday,
                settings=settings,
            ),
            ephemeral=True,
        )

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
        await interaction.followup.send(
            embed=_build_birthday_embed(
                title="Your birthday settings",
                description="This record stays scoped to the current server.",
                birthday=birthday,
                settings=settings,
            ),
            ephemeral=True,
        )

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
        resolved = await _resolve_birthday_members(interaction.guild, upcoming)
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
        _set_resolution_footer(
            embed,
            total_candidates=len(upcoming),
            shown_count=len(lines),
            resolved_count=len(resolved),
            requested_limit=limit,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="today",
        description="Show birthdays currently active under Bdayblaze celebration logic.",
    )
    @app_commands.guild_only()
    async def today(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        active = await self._birthday_service.list_current_birthdays(
            interaction.guild.id,
            limit=_PUBLIC_RESULT_LIMIT * 3,
        )
        resolved = await _resolve_birthday_members(interaction.guild, active)
        if not resolved:
            await interaction.followup.send(
                (
                "No birthdays are currently active under Bdayblaze's celebration logic. "
                "This is not a simple server-midnight list."
            ),
                ephemeral=True,
            )
            return

        now_utc = datetime.now(UTC)
        active_lines: list[tuple[datetime, str]] = []
        for preview, member in resolved:
            window = current_celebration_window_utc(
                birth_month=preview.birth_month,
                birth_day=preview.birth_day,
                timezone_name=preview.effective_timezone,
                now_utc=now_utc,
            )
            if window is None:
                continue
            _, ends_at = window
            active_lines.append(
                (
                    ends_at,
                    (
                        f"{member.mention} - {preview.birth_month:02d}/{preview.birth_day:02d} - "
                        f"ends {discord.utils.format_dt(ends_at, 'R')}"
                    ),
                )
            )
        active_lines.sort(key=lambda item: item[0])
        shown_lines = [line for _, line in active_lines[:_PUBLIC_RESULT_LIMIT]]
        if not shown_lines:
            await interaction.followup.send(
                (
                    "No birthdays are currently active under Bdayblaze's celebration logic. "
                    "This is not a simple server-midnight list."
                ),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Birthdays active right now",
            description=(
                "These are birthdays currently active under Bdayblaze's celebration logic, "
                "not a simple server-midnight list.\n\n"
                + "\n".join(shown_lines)
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc,
        )
        _set_resolution_footer(
            embed,
            total_candidates=len(active),
            shown_count=len(shown_lines),
            resolved_count=len(resolved),
            requested_limit=_PUBLIC_RESULT_LIMIT,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="next",
        description="Show the nearest upcoming birthday in this server.",
    )
    @app_commands.guild_only()
    async def next_birthday(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        upcoming = await self._birthday_service.list_upcoming_birthdays(interaction.guild.id, 12)
        resolved = await _resolve_birthday_members(interaction.guild, upcoming)
        if not resolved:
            await interaction.followup.send(
                "No upcoming birthdays are registered in this server yet.",
                ephemeral=True,
            )
            return
        preview, member = resolved[0]
        embed = discord.Embed(
            title="Next birthday",
            description=(
                f"{member.mention} is next on {preview.birth_month:02d}/{preview.birth_day:02d}.\n"
                "Celebration starts "
                f"{discord.utils.format_dt(preview.next_occurrence_at_utc, 'R')}."
            ),
            color=discord.Color.blurple(),
        )
        if len(resolved) < len(upcoming):
            embed.set_footer(text="Some members could not be resolved and were skipped.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="month",
        description="Browse birthdays registered for a month in this server.",
    )
    @app_commands.describe(month="Month number to browse. Defaults to this server's current month.")
    @app_commands.guild_only()
    async def month_birthdays(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12] | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        settings = await self._settings_service.get_settings(interaction.guild.id)
        selected_month = month or _current_month(settings.default_timezone)
        previews = await self._birthday_service.list_birthdays_for_month(
            interaction.guild.id,
            month=selected_month,
            limit=_PUBLIC_RESULT_LIMIT + 6,
            order_by_upcoming=False,
        )
        resolved = await _resolve_birthday_members(interaction.guild, previews)
        shown = resolved[:_PUBLIC_RESULT_LIMIT]
        if not shown:
            await interaction.followup.send(
                f"No birthdays are registered for {month_name[selected_month]} yet.",
                ephemeral=True,
            )
            return
        lines = [
            f"{preview.birth_day:02d} - {member.mention}"
            for preview, member in shown
        ]
        embed = discord.Embed(
            title=f"Birthdays in {month_name[selected_month]}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        _set_resolution_footer(
            embed,
            total_candidates=len(previews),
            shown_count=len(lines),
            resolved_count=len(resolved),
            requested_limit=_PUBLIC_RESULT_LIMIT,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="twins",
        description="Find members in this server who share your birthday month and day.",
    )
    @app_commands.guild_only()
    async def twins(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            birthday, twins = await self._birthday_service.list_birthday_twins(
                interaction.guild.id,
                interaction.user.id,
                limit=_PUBLIC_RESULT_LIMIT + 6,
            )
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        resolved = await _resolve_birthday_members(interaction.guild, twins)
        if not resolved:
            await interaction.followup.send(
                (
                    "No birthday twins found for "
                    f"{birthday.birth_month:02d}/{birthday.birth_day:02d} "
                    "in this server yet."
                ),
                ephemeral=True,
            )
            return
        lines = [
            f"{member.mention} - {preview.birth_month:02d}/{preview.birth_day:02d}"
            for preview, member in resolved[:_PUBLIC_RESULT_LIMIT]
        ]
        embed = discord.Embed(
            title="Birthday twins",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        _set_resolution_footer(
            embed,
            total_candidates=len(twins),
            shown_count=len(lines),
            resolved_count=len(resolved),
            requested_limit=_PUBLIC_RESULT_LIMIT,
        )
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
        name="test-message",
        description="Send a private preview of the current birthday announcement.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def test_message(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        settings = await self._settings_service.get_settings(interaction.guild.id)
        readiness = await self._settings_service.describe_announcement_delivery(
            interaction.guild
        )
        status_embed = discord.Embed(
            title="Birthday message test",
            description="Preview only. No live birthday announcement was sent.",
            color=discord.Color.green()
            if readiness.status == "ready"
            else discord.Color.orange(),
        )
        status_embed.add_field(
            name="Live delivery readiness",
            value=readiness.summary,
            inline=False,
        )
        if readiness.details:
            status_embed.add_field(
                name="Details",
                value="\n".join(readiness.details),
                inline=False,
            )
        status_embed.add_field(
            name="Current presentation",
            value=(
                f"Mode: {celebration_mode_label(settings.celebration_mode)}\n"
                f"Theme: {announcement_theme_label(settings.announcement_theme)}"
            ),
            inline=False,
        )
        single_preview = build_announcement_message(
            server_name=interaction.guild.name,
            recipients=preview_single_recipients(),
            celebration_mode=settings.celebration_mode,
            announcement_theme=settings.announcement_theme,
            template=settings.announcement_template,
            preview_label="Preview only - single birthday example",
        )
        batch_preview = build_announcement_message(
            server_name=interaction.guild.name,
            recipients=preview_batch_recipients(),
            celebration_mode=settings.celebration_mode,
            announcement_theme=settings.announcement_theme,
            template=settings.announcement_template,
            preview_label="Preview only - multi-birthday example",
        )
        embeds = [status_embed, single_preview.embed, batch_preview.embed]
        try:
            await interaction.user.send(
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            status_embed.set_footer(text="DMs were closed, so the preview is shown here instead.")
            await interaction.followup.send(
                embeds=embeds,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.followup.send(
            "Preview sent to your DMs. No live birthday announcement was posted.",
            ephemeral=True,
        )

    @app_commands.command(
        name="list",
        description="Privately browse saved birthdays for this server.",
    )
    @app_commands.describe(
        month="Optional month filter",
        limit="How many birthdays to show",
        order="Choose calendar order or upcoming order",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def list_birthdays(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12] | None = None,
        limit: app_commands.Range[int, 1, _ADMIN_RESULT_LIMIT] = 10,
        order: Literal["calendar", "upcoming"] = "calendar",
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        fetch_limit = min(limit + 6, _ADMIN_RESULT_LIMIT + 6)
        if month is None:
            previews = await self._birthday_service.list_birthdays(
                interaction.guild.id,
                limit=fetch_limit,
                order_by_upcoming=order == "upcoming",
            )
            title = (
                "Saved birthdays by next celebration"
                if order == "upcoming"
                else "Saved birthdays by calendar date"
            )
        else:
            previews = await self._birthday_service.list_birthdays_for_month(
                interaction.guild.id,
                month=month,
                limit=fetch_limit,
                order_by_upcoming=order == "upcoming",
            )
            title = f"Saved birthdays in {month_name[month]}"
        resolved = await _resolve_birthday_members(interaction.guild, previews)
        shown = resolved[:limit]
        if not shown:
            await interaction.followup.send(
                "No saved birthdays matched that filter.",
                ephemeral=True,
            )
            return
        lines = [
            _format_birthday_list_line(preview, member, order=order)
            for preview, member in shown
        ]
        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=datetime.now(UTC),
        )
        _set_resolution_footer(
            embed,
            total_candidates=len(previews),
            shown_count=len(lines),
            resolved_count=len(resolved),
            requested_limit=limit,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @member.command(name="view", description="Privately view another member's saved birthday.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def member_view(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            birthday = await self._birthday_service.require_birthday(
                interaction.guild.id,
                member.id,
                missing_message=(
                    f"{member.display_name} does not have a saved birthday in this server."
                ),
            )
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.followup.send(
            embed=_build_birthday_embed(
                title=f"{member.display_name}'s birthday",
                description="This stored record is scoped to the current server.",
                birthday=birthday,
                settings=settings,
            ),
            ephemeral=True,
        )

    @member.command(name="set", description="Privately set or update another member's birthday.")
    @app_commands.describe(
        member="Member whose birthday you want to manage",
        month="Birth month as a number",
        day="Birth day as a number",
        year="Optional birth year",
        timezone="Optional IANA timezone override",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def member_set(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
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
                user_id=member.id,
                month=month,
                day=day,
                birth_year=year,
                timezone_override=timezone,
            )
        except ValidationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.followup.send(
            embed=_build_birthday_embed(
                title=f"Birthday saved for {member.display_name}",
                description="This record stays scoped to the current server.",
                birthday=birthday,
                settings=settings,
            ),
            ephemeral=True,
        )

    @member_set.autocomplete("timezone")
    async def member_set_timezone_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self.birthday_timezone_autocomplete(interaction, current)

    @member.command(name="remove", description="Privately remove another member's saved birthday.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def member_remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await self._birthday_service.remove_member_birthday(
                interaction.guild.id,
                member.id,
                missing_message=(
                    f"{member.display_name} does not have a saved birthday in this server."
                ),
            )
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await _remove_active_birthday_role_if_needed(
            interaction.guild,
            member.id,
            deleted.active_birthday_role_id,
            reason="Bdayblaze admin removed birthday data",
        )
        await interaction.followup.send(
            f"Removed {member.mention}'s stored birthday data for this server.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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
        await _remove_active_birthday_role_if_needed(
            interaction.guild,
            interaction.user.id,
            deleted.active_birthday_role_id,
            reason="Bdayblaze user deleted birthday data",
        )
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


def _build_birthday_embed(
    *,
    title: str,
    description: str,
    birthday: MemberBirthday,
    settings: GuildSettings,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Date",
        value=f"{birthday.birth_month:02d}/{birthday.birth_day:02d}",
        inline=True,
    )
    embed.add_field(name="Timezone", value=birthday.effective_timezone(settings), inline=True)
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
    return embed


async def _resolve_birthday_members(
    guild: discord.Guild,
    previews: list[BirthdayPreview],
) -> list[tuple[BirthdayPreview, discord.Member]]:
    resolved_members = await resolve_guild_members(guild, (preview.user_id for preview in previews))
    by_user_id = {user_id: member for user_id, member in resolved_members}
    return [
        (preview, member)
        for preview in previews
        if (member := by_user_id.get(preview.user_id)) is not None
    ]


def _format_birthday_list_line(
    preview: BirthdayPreview,
    member: discord.Member,
    *,
    order: Literal["calendar", "upcoming"],
) -> str:
    if order == "upcoming":
        return (
            f"{member.mention} - {preview.birth_month:02d}/{preview.birth_day:02d} - "
            f"{discord.utils.format_dt(preview.next_occurrence_at_utc, 'R')}"
        )
    return f"{preview.birth_month:02d}/{preview.birth_day:02d} - {member.mention}"


def _set_resolution_footer(
    embed: discord.Embed,
    *,
    total_candidates: int,
    shown_count: int,
    resolved_count: int,
    requested_limit: int,
) -> None:
    notes: list[str] = []
    if total_candidates > requested_limit and shown_count >= requested_limit:
        notes.append(f"Showing {shown_count} results.")
    if resolved_count < total_candidates:
        notes.append("Some members could not be resolved and were skipped.")
    if notes:
        embed.set_footer(text=" ".join(notes))


def _current_month(default_timezone: str) -> int:
    try:
        return datetime.now(UTC).astimezone(ZoneInfo(default_timezone)).month
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).month


async def _remove_active_birthday_role_if_needed(
    guild: discord.Guild,
    user_id: int,
    role_id: int | None,
    *,
    reason: str,
) -> None:
    if role_id is None:
        return
    role = guild.get_role(role_id)
    if role is None:
        return
    resolved = await resolve_guild_members(guild, (user_id,))
    if not resolved:
        return
    _, member = resolved[0]
    if role not in member.roles:
        return
    try:
        await member.remove_roles(role, reason=reason)
    except (discord.Forbidden, discord.HTTPException):
        return
