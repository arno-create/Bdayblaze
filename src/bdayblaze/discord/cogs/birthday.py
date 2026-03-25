from __future__ import annotations

import io
from calendar import month_name
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.discord.announcements import build_announcement_message
from bdayblaze.discord.embed_budget import BudgetedEmbed, truncate_text
from bdayblaze.discord.member_resolution import resolve_guild_members
from bdayblaze.discord.studio_audit import StudioAuditLogger
from bdayblaze.discord.ui.setup import (
    MessageTemplateView,
    SetupView,
    build_message_template_embed,
    build_setup_embed,
)
from bdayblaze.domain.announcement_template import (
    AnnouncementRenderRecipient,
    anniversary_years,
    preview_context_for_kind,
)
from bdayblaze.domain.announcement_theme import announcement_theme_label
from bdayblaze.domain.birthday_logic import LATE_CELEBRATION_NOTE, is_birthday_active_now
from bdayblaze.domain.media_validation import assess_media_url
from bdayblaze.domain.models import (
    BirthdayCelebration,
    BirthdayPreview,
    BirthdayQuestStatus,
    BirthdayTimeline,
    BirthdayWish,
    GuildAnalytics,
    GuildSettings,
    MemberBirthday,
    NitroConciergeEntry,
)
from bdayblaze.domain.timezones import autocomplete_timezones
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.content_policy import ContentPolicyError, ensure_safe_announcement_inputs
from bdayblaze.services.diagnostics import build_presentation_diagnostics
from bdayblaze.services.errors import NotFoundError, ValidationError
from bdayblaze.services.experience_service import ExperienceService
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.settings_service import SettingsService

_PUBLIC_RESULT_LIMIT = 12
_ADMIN_RESULT_LIMIT = 20
_IMPORT_SIZE_CAP_BYTES = 128 * 1024


class BirthdayGroup(
    commands.GroupCog,
    group_name="birthday",
    group_description="Manage your birthday and server birthday settings",
):
    member = app_commands.Group(
        name="member",
        description="Privately manage another member's birthday record.",
    )
    anniversary = app_commands.Group(
        name="anniversary",
        description="Manage join-anniversary settings and sync.",
    )
    event = app_commands.Group(
        name="event",
        description="Manage recurring annual celebrations.",
    )
    wish = app_commands.Group(
        name="wish",
        description="Queue or manage Birthday Capsule wishes.",
    )
    capsule = app_commands.Group(
        name="capsule",
        description="Preview Birthday Capsule state privately.",
    )
    quest = app_commands.Group(
        name="quest",
        description="Check Birthday Quest progress and check in.",
    )
    surprise = app_commands.Group(
        name="surprise",
        description="Manage Birthday Surprise fulfillment.",
    )

    def __init__(
        self,
        birthday_service: BirthdayService,
        experience_service: ExperienceService,
        settings_service: SettingsService,
        health_service: HealthService,
        studio_audit_logger: StudioAuditLogger,
    ) -> None:
        super().__init__()
        self._birthday_service = birthday_service
        self._experience_service = experience_service
        self._settings_service = settings_service
        self._health_service = health_service
        self._studio_audit_logger = studio_audit_logger

    @app_commands.command(
        name="set",
        description="Save or update your birthday profile for this server.",
    )
    @app_commands.describe(
        month="Birth month as a number",
        day="Birth day as a number",
        year="Optional birth year. Leave blank to keep it private.",
        timezone="Optional IANA timezone like Asia/Yerevan.",
        visibility="Whether other members can see you in browse commands in this server.",
    )
    @app_commands.guild_only()
    async def set_birthday(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12],
        day: app_commands.Range[int, 1, 31],
        year: app_commands.Range[int, 1900, 9999] | None = None,
        timezone: str | None = None,
        visibility: Literal["private", "server_visible"] = "private",
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
                profile_visibility=visibility,
            )
            await self._birthday_service.sync_member_anniversary(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                joined_at_utc=getattr(interaction.user, "joined_at", None),
                source="birthday_profile",
            )
        except ValidationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.followup.send(
            embed=_build_birthday_embed(
                title="Birthday saved",
                description="Your birthday stays stored only for this server.",
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

    @app_commands.command(
        name="view",
        description="View your saved birthday profile for this server.",
    )
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
                title="Your birthday profile",
                description="This record is server-scoped and privacy-first by default.",
                birthday=birthday,
                settings=settings,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="remove",
        description="Delete your saved birthday for this server.",
    )
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Delete your birthday data for this server?",
            ephemeral=True,
            view=ConfirmBirthdayDeletionView(self._birthday_service, interaction.user.id),
        )

    @app_commands.command(
        name="upcoming",
        description="See upcoming visible birthdays in this server.",
    )
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
            visible_only=True,
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
                "No visible upcoming birthdays are registered in this server yet.",
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
            visible_only=True,
        )
        resolved = await _resolve_birthday_members(interaction.guild, active)
        if not resolved:
            await interaction.followup.send(
                (
                    "No visible birthdays are currently active under Bdayblaze's celebration "
                    "logic. This is not a simple server-midnight list."
                ),
                ephemeral=True,
            )
            return
        lines = [
            f"{preview.birth_month:02d}/{preview.birth_day:02d} - {member.mention}"
            for preview, member in resolved[:_PUBLIC_RESULT_LIMIT]
        ]
        embed = discord.Embed(
            title="Birthdays active right now",
            description=(
                "These are birthdays currently active under Bdayblaze's celebration logic, "
                "not a simple server-midnight list.\n\n" + "\n".join(lines)
            ),
            color=discord.Color.blurple(),
        )
        _set_resolution_footer(
            embed,
            total_candidates=len(active),
            shown_count=len(lines),
            resolved_count=len(resolved),
            requested_limit=_PUBLIC_RESULT_LIMIT,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="next", description="Show the nearest visible upcoming birthday.")
    @app_commands.guild_only()
    async def next_birthday(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        upcoming = await self._birthday_service.list_upcoming_birthdays(
            interaction.guild.id,
            12,
            visible_only=True,
        )
        resolved = await _resolve_birthday_members(interaction.guild, upcoming)
        if not resolved:
            await interaction.followup.send(
                "No visible upcoming birthdays are registered in this server yet.",
                ephemeral=True,
            )
            return
        preview, member = resolved[0]
        embed = discord.Embed(
            title="Next birthday",
            description=(
                f"{member.mention} is next on "
                f"{preview.birth_month:02d}/{preview.birth_day:02d}.\n"
                "Celebration starts "
                f"{discord.utils.format_dt(preview.next_occurrence_at_utc, 'R')}."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="month",
        description="Browse birthdays registered for a month in this server.",
    )
    @app_commands.describe(
        month="Month number to browse. Defaults to the current month in the server timezone.",
        scope="Admins can choose all saved birthdays; everyone else uses visible birthdays only.",
    )
    @app_commands.guild_only()
    async def month_birthdays(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12] | None = None,
        scope: Literal["visible", "all"] = "visible",
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        visible_only = _visible_only_for_scope(interaction, scope)
        settings = await self._settings_service.get_settings(interaction.guild.id)
        selected_month = month or _current_month(settings.default_timezone)
        previews = await self._birthday_service.list_birthdays_for_month(
            interaction.guild.id,
            month=selected_month,
            limit=_PUBLIC_RESULT_LIMIT + 6,
            order_by_upcoming=False,
            visible_only=visible_only,
        )
        resolved = await _resolve_birthday_members(interaction.guild, previews)
        shown = resolved[:_PUBLIC_RESULT_LIMIT]
        if not shown:
            await interaction.followup.send(
                f"No birthdays matched {month_name[selected_month]} for that scope.",
                ephemeral=True,
            )
            return
        lines = [f"{preview.birth_day:02d} - {member.mention}" for preview, member in shown]
        embed = discord.Embed(
            title=f"Birthdays in {month_name[selected_month]}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        leaderboard = await self._birthday_service.month_leaderboard(
            interaction.guild.id,
            month=selected_month,
            visible_only=visible_only,
        )
        if leaderboard:
            embed.add_field(
                name="Busiest dates",
                value="\n".join(
                    f"{selected_month:02d}/{day:02d} - {count} member(s)"
                    for day, count in leaderboard
                ),
                inline=False,
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
                visible_only=True,
            )
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        resolved = await _resolve_birthday_members(interaction.guild, twins)
        if not resolved:
            await interaction.followup.send(
                (
                    "No visible birthday twins found for "
                    f"{birthday.birth_month:02d}/{birthday.birth_day:02d} yet."
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
        await interaction.followup.send(embed=embed, ephemeral=True)

    @wish.command(name="add", description="Queue a private Birthday Capsule wish for someone here.")
    @app_commands.describe(
        member="Member whose birthday capsule you want to add to",
        message="Wish text that unlocks on their birthday",
        link="Optional safe HTTPS link or GIF URL",
    )
    @app_commands.guild_only()
    async def wish_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        message: app_commands.Range[str, 1, 350],
        link: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        wish = await self._experience_service.add_wish(
            guild_id=interaction.guild.id,
            author_user_id=interaction.user.id,
            target_user_id=member.id,
            wish_text=message,
            link_url=link,
        )
        embed = discord.Embed(
            title="Birthday wish queued",
            description=(
                f"Your wish for {member.mention} is saved privately until their birthday.\n"
                "Adding another wish for the same member overwrites your earlier unrevealed one."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Wish", value=wish.wish_text, inline=False)
        if wish.link_url is not None:
            embed.add_field(
                name="Link",
                value=_format_wish_link_value(wish.link_url),
                inline=False,
            )
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @wish.command(name="list", description="Privately list your queued Birthday Capsule wishes.")
    @app_commands.guild_only()
    async def wish_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        wishes = await self._experience_service.list_author_wishes(
            interaction.guild.id,
            interaction.user.id,
        )
        if not wishes:
            await interaction.followup.send(
                "You do not have any queued birthday wishes in this server.",
                ephemeral=True,
            )
            return
        resolved = await resolve_guild_members(
            interaction.guild,
            [wish.target_user_id for wish in wishes],
        )
        members_by_id = {user_id: member for user_id, member in resolved}
        await interaction.followup.send(
            embed=_build_wish_list_embed(wishes, members_by_id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @wish.command(name="remove", description="Remove an unrevealed birthday wish.")
    @app_commands.describe(
        member="Member whose capsule wish should be removed",
        author="Admin only: remove a specific author's queued wish",
    )
    @app_commands.guild_only()
    async def wish_remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        author: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        is_admin = _is_manage_guild(interaction)
        if author is not None and not is_admin:
            raise ValidationError("Only server admins can remove someone else's queued wish.")
        await self._experience_service.remove_wish(
            guild_id=interaction.guild.id,
            actor_user_id=interaction.user.id,
            target_user_id=member.id,
            author_user_id=author.id if author is not None else None,
            moderated=author is not None,
        )
        if author is None:
            message = f"Removed your queued wish for {member.mention}."
        else:
            message = f"Removed {author.mention}'s queued wish for {member.mention}."
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @capsule.command(name="preview", description="Preview a Birthday Capsule privately.")
    @app_commands.describe(
        member="Defaults to you. Admins can preview another member's queued or unlocked capsule.",
    )
    @app_commands.guild_only()
    async def capsule_preview(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        target = member or interaction.user
        is_admin = _is_manage_guild(interaction)
        if target.id != interaction.user.id and not is_admin:
            raise ValidationError("You can only preview your own Birthday Capsule.")
        celebration, wishes, queued_count = await self._experience_service.list_capsule_preview(
            guild_id=interaction.guild.id,
            target_user_id=target.id,
            include_private_queued=is_admin,
        )
        resolved = await resolve_guild_members(
            interaction.guild,
            [wish.author_user_id for wish in wishes],
        )
        authors_by_id = {user_id: author for user_id, author in resolved}
        await interaction.followup.send(
            embed=_build_capsule_preview_embed(
                target,
                celebration=celebration,
                wishes=wishes,
                queued_count=queued_count,
                authors_by_id=authors_by_id,
                viewer_is_admin=is_admin,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @quest.command(name="status", description="Check your Birthday Quest progress.")
    @app_commands.guild_only()
    async def quest_status(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        status = await self._experience_service.get_quest_status(
            interaction.guild.id,
            interaction.user.id,
        )
        if await _refresh_live_quest_progress(
            interaction,
            status.celebration,
        ):
            status = await self._experience_service.get_quest_status(
                interaction.guild.id,
                interaction.user.id,
            )
        await interaction.followup.send(
            embed=_build_quest_status_embed(status),
            ephemeral=True,
        )

    @quest.command(name="check-in", description="Check in for your active Birthday Quest.")
    @app_commands.guild_only()
    async def quest_check_in(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        celebration = await self._experience_service.check_in_quest(
            interaction.guild.id,
            interaction.user.id,
        )
        status = await self._experience_service.get_quest_status(
            interaction.guild.id,
            interaction.user.id,
        )
        if await _refresh_live_quest_progress(
            interaction,
            status.celebration,
        ):
            status = await self._experience_service.get_quest_status(
                interaction.guild.id,
                interaction.user.id,
            )
            if status.celebration is not None:
                celebration = status.celebration
        await interaction.followup.send(
            embed=_build_quest_status_embed(status, celebration_override=celebration),
            ephemeral=True,
        )

    @app_commands.command(
        name="timeline",
        description="View a birthday profile, countdown, and celebration timeline.",
    )
    @app_commands.describe(
        member="Defaults to you. Private profiles stay private unless you manage the server.",
    )
    @app_commands.guild_only()
    async def timeline(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        target = member or interaction.user
        timeline = await self._experience_service.build_timeline(
            guild_id=interaction.guild.id,
            target_user_id=target.id,
            viewer_user_id=interaction.user.id,
            admin_override=_is_manage_guild(interaction),
        )
        if await _refresh_live_quest_progress(
            interaction,
            timeline.active_celebration,
        ):
            timeline = await self._experience_service.build_timeline(
                guild_id=interaction.guild.id,
                target_user_id=target.id,
                viewer_user_id=interaction.user.id,
                admin_override=_is_manage_guild(interaction),
            )
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.followup.send(
            embed=_build_timeline_embed(
                target,
                timeline,
                active_now=_timeline_is_active_now(timeline, settings),
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="analytics",
        description="View lightweight admin analytics for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def analytics(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        analytics = await self._experience_service.fetch_analytics(interaction.guild.id)
        await interaction.followup.send(
            embed=_build_analytics_embed(analytics),
            ephemeral=True,
        )

    @surprise.command(
        name="queue",
        description="List pending manual Nitro concierge fulfillments.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def surprise_queue(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        entries = await self._experience_service.list_pending_nitro(interaction.guild.id)
        resolved = await resolve_guild_members(
            interaction.guild,
            [entry.user_id for entry in entries],
        )
        members_by_id = {user_id: member for user_id, member in resolved}
        await interaction.followup.send(
            embed=_build_nitro_queue_embed(entries, members_by_id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @surprise.command(
        name="fulfill",
        description="Mark a manual Nitro concierge record as delivered or not delivered.",
    )
    @app_commands.describe(
        celebration_id="Celebration ID shown in /birthday surprise queue",
        status="Manual fulfillment result",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def surprise_fulfill(
        self,
        interaction: discord.Interaction,
        celebration_id: int,
        status: Literal["delivered", "not_delivered"],
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        celebration = await self._experience_service.fulfill_nitro(
            interaction.guild.id,
            celebration_id,
            admin_user_id=interaction.user.id,
            delivered=status == "delivered",
        )
        await interaction.followup.send(
            (
                f"Nitro concierge record `{celebration.id}` marked as "
                f"`{celebration.nitro_fulfillment_status}`."
            ),
            ephemeral=True,
        )

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
                birthday_service=self._birthday_service,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="studio", description="Open Celebration Studio.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def studio(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await self._settings_service.get_settings(interaction.guild.id)
        experience_settings = await self._experience_service.get_settings(interaction.guild.id)
        surprise_rewards = tuple(
            await self._experience_service.list_surprise_rewards(interaction.guild.id)
        )
        server_anniversary = await self._birthday_service.get_server_anniversary(
            interaction.guild.id
        )
        recurring_events = tuple(
            await self._birthday_service.list_recurring_celebrations(
                interaction.guild.id,
                limit=8,
            )
        )
        await interaction.response.send_message(
            embed=build_message_template_embed(
                settings,
                section="home",
                guild=interaction.guild,
                experience_settings=experience_settings,
                surprise_rewards=surprise_rewards,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self._settings_service,
                experience_service=self._experience_service,
                settings=settings,
                owner_id=interaction.user.id,
                guild=interaction.guild,
                birthday_service=self._birthday_service,
                section="home",
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="test-message",
        description="Send a private dry-run preview for any supported celebration type.",
    )
    @app_commands.describe(
        kind="Which delivery type to preview",
        member="Optional member to preview with for birthday or anniversary tests",
        event_id="Recurring event id for recurring-event previews",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def test_message(
        self,
        interaction: discord.Interaction,
        kind: Literal[
            "birthday_announcement",
            "birthday_dm",
            "anniversary",
            "server_anniversary",
            "recurring_event",
        ] = "birthday_announcement",
        member: discord.Member | None = None,
        event_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        settings = await self._settings_service.get_settings(interaction.guild.id)
        recurring_channel_id: int | None = None
        if kind == "recurring_event" and event_id is not None:
            try:
                recurring_channel_id = (
                    await self._birthday_service.get_recurring_celebration(
                        interaction.guild.id,
                        event_id,
                    )
                ).channel_id
            except NotFoundError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
        if kind == "server_anniversary":
            anniversary = await self._birthday_service.get_server_anniversary(
                interaction.guild.id
            )
            recurring_channel_id = anniversary.channel_id if anniversary is not None else None
        readiness = await self._settings_service.describe_delivery(
            interaction.guild,
            kind=kind,
            channel_id=recurring_channel_id,
        )
        preview_error: str | None = None
        try:
            preview_embed = await _build_preview_embed(
                interaction.guild,
                settings,
                self._birthday_service,
                kind=kind,
                member=member,
                event_id=event_id,
            )
        except (ValidationError, NotFoundError, ValueError) as exc:
            preview_error = str(exc)
            preview_embed = _build_preview_unavailable_embed(
                _preview_kind_label(kind),
                preview_error,
            )
        await interaction.followup.send(
            embeds=[
                _build_dry_run_status_embed(
                    readiness,
                    settings,
                    kind=kind,
                    channel_id=recurring_channel_id,
                    preview_member_count=_preview_member_count(kind=kind, member=member),
                    preview_error=preview_error,
                ),
                preview_embed,
            ],
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="list",
        description="Privately browse saved birthdays for this server.",
    )
    @app_commands.describe(
        month="Optional month filter",
        limit="How many birthdays to show",
        order="Choose calendar order or upcoming order",
        scope="Visible birthdays for everyone, or all birthdays for admins.",
    )
    @app_commands.guild_only()
    async def list_birthdays(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12] | None = None,
        limit: app_commands.Range[int, 1, _ADMIN_RESULT_LIMIT] = 10,
        order: Literal["calendar", "upcoming"] = "calendar",
        scope: Literal["visible", "all"] = "visible",
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        visible_only = _visible_only_for_scope(interaction, scope)
        fetch_limit = min(limit + 6, _ADMIN_RESULT_LIMIT + 6)
        if month is None:
            previews = await self._birthday_service.list_birthdays(
                interaction.guild.id,
                limit=fetch_limit,
                order_by_upcoming=order == "upcoming",
                visible_only=visible_only,
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
                visible_only=visible_only,
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
            _format_birthday_list_line(preview, member, order=order) for preview, member in shown
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

    @app_commands.command(
        name="export",
        description="Export this server's birthdays as a private CSV.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def export_birthdays(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        csv_text = await self._birthday_service.export_birthdays_csv(interaction.guild.id)
        file = discord.File(
            io.BytesIO(csv_text.encode("utf-8")),
            filename=f"bdayblaze-birthdays-{interaction.guild.id}.csv",
        )
        await interaction.followup.send(
            "Private export ready.",
            file=file,
            ephemeral=True,
        )

    @app_commands.command(
        name="import",
        description="Preview or apply a birthday CSV import for this server.",
    )
    @app_commands.describe(
        attachment="CSV attachment exported by Bdayblaze",
        apply_token="Leave blank for a preview. Re-run with the preview token to apply.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def import_birthdays(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        apply_token: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        if attachment.size > _IMPORT_SIZE_CAP_BYTES:
            await interaction.followup.send(
                f"CSV imports must be {_IMPORT_SIZE_CAP_BYTES // 1024} KiB or smaller.",
                ephemeral=True,
            )
            return
        csv_text = (await attachment.read()).decode("utf-8", errors="replace")
        preview, allowed_user_ids = await _build_validated_import_preview(
            interaction.guild,
            self._birthday_service,
            csv_text,
        )
        if apply_token is None:
            await interaction.followup.send(
                embed=_build_import_preview_embed(preview),
                ephemeral=True,
            )
            return
        try:
            preview = await self._birthday_service.apply_birthdays_import(
                interaction.guild.id,
                csv_text=csv_text,
                apply_token=apply_token.strip(),
                allowed_user_ids=allowed_user_ids,
            )
        except ValidationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=_build_import_preview_embed(preview, applied=True),
            ephemeral=True,
        )

    @member.command(
        name="view",
        description="Privately view another member's saved birthday.",
    )
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

    @member.command(
        name="set",
        description="Privately set or update another member's birthday.",
    )
    @app_commands.describe(
        member="Member whose birthday you want to manage",
        visibility="Whether this member appears in visible browse commands for this server.",
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
        visibility: Literal["private", "server_visible"] = "private",
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
                profile_visibility=visibility,
            )
            await self._birthday_service.sync_member_anniversary(
                guild_id=interaction.guild.id,
                user_id=member.id,
                joined_at_utc=member.joined_at,
                source="admin_member_set",
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
        _: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=timezone_name, value=timezone_name)
            for timezone_name in autocomplete_timezones(current)
        ]

    @member.command(
        name="remove",
        description="Privately remove another member's saved birthday.",
    )
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

    @anniversary.command(
        name="settings",
        description="Update join-anniversary delivery settings.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def anniversary_settings(
        self,
        interaction: discord.Interaction,
        enabled: bool | None = None,
        channel: discord.TextChannel | None = None,
        use_announcement_channel: bool = False,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        if channel is not None and use_announcement_channel:
            await interaction.followup.send(
                "Choose an anniversary channel or use the main announcement channel, not both.",
                ephemeral=True,
            )
            return
        try:
            if enabled is None and use_announcement_channel:
                await self._settings_service.update_settings(
                    interaction.guild,
                    anniversary_channel_id=None,
                )
            elif enabled is None and channel is not None:
                await self._settings_service.update_settings(
                    interaction.guild,
                    anniversary_channel_id=channel.id,
                )
            elif enabled is not None and use_announcement_channel:
                await self._settings_service.update_settings(
                    interaction.guild,
                    anniversary_enabled=enabled,
                    anniversary_channel_id=None,
                )
            elif enabled is not None and channel is not None:
                await self._settings_service.update_settings(
                    interaction.guild,
                    anniversary_enabled=enabled,
                    anniversary_channel_id=channel.id,
                )
            elif enabled is not None:
                await self._settings_service.update_settings(
                    interaction.guild,
                    anniversary_enabled=enabled,
                )
        except ValidationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        latest = await self._settings_service.get_settings(interaction.guild.id)
        server_anniversary = await self._birthday_service.get_server_anniversary(
            interaction.guild.id
        )
        recurring_events = tuple(
            await self._birthday_service.list_recurring_celebrations(
                interaction.guild.id,
                limit=8,
            )
        )
        await interaction.followup.send(
            embed=build_message_template_embed(
                latest,
                note="Member anniversary routing updated.",
                section="anniversary",
                guild=interaction.guild,
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            view=MessageTemplateView(
                settings_service=self._settings_service,
                settings=latest,
                owner_id=interaction.user.id,
                guild=interaction.guild,
                birthday_service=self._birthday_service,
                section="anniversary",
                server_anniversary=server_anniversary,
                recurring_events=recurring_events,
            ),
            ephemeral=True,
        )

    @anniversary.command(
        name="sync",
        description="Track join anniversaries for a member or stored birthdays.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def anniversary_sync(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        synced = 0
        if member is not None:
            try:
                await self._birthday_service.sync_member_anniversary(
                    guild_id=interaction.guild.id,
                    user_id=member.id,
                    joined_at_utc=member.joined_at,
                    source="admin_sync",
                )
            except ValidationError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            synced = 1
        else:
            user_ids = await self._birthday_service.list_member_birthday_user_ids(
                interaction.guild.id
            )
            if not user_ids:
                await interaction.followup.send(
                    "No stored birthdays were found to sync from yet.",
                    ephemeral=True,
                )
                return
            resolved = await resolve_guild_members(interaction.guild, user_ids)
            for _, resolved_member in resolved:
                try:
                    await self._birthday_service.sync_member_anniversary(
                        guild_id=interaction.guild.id,
                        user_id=resolved_member.id,
                        joined_at_utc=resolved_member.joined_at,
                        source="admin_sync",
                    )
                except ValidationError:
                    continue
                synced += 1
        await interaction.followup.send(
            f"Tracked join anniversaries refreshed for {synced} member(s).",
            ephemeral=True,
        )

    @event.command(name="add", description="Create a recurring annual celebration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def event_add(
        self,
        interaction: discord.Interaction,
        name: str,
        month: app_commands.Range[int, 1, 12],
        day: app_commands.Range[int, 1, 31],
        channel: discord.TextChannel | None = None,
        template: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            await _require_ready_delivery(
                self._settings_service,
                interaction.guild,
                kind="recurring_event",
                channel_id=channel.id if channel is not None else None,
            )
            celebration = await self._birthday_service.upsert_recurring_celebration(
                guild_id=interaction.guild.id,
                celebration_id=None,
                name=name,
                month=month,
                day=day,
                channel_id=channel.id if channel is not None else None,
                template=template,
                enabled=True,
            )
        except ContentPolicyError as exc:
            await _audit_blocked_attempt(
                self._studio_audit_logger,
                interaction,
                surface="recurring_event_add",
                error=exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except ValidationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"Recurring event `{celebration.name}` saved with id `{celebration.id}`.",
            ephemeral=True,
        )

    @event.command(name="edit", description="Edit a recurring annual celebration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def event_edit(
        self,
        interaction: discord.Interaction,
        event_id: int,
        name: str | None = None,
        month: app_commands.Range[int, 1, 12] | None = None,
        day: app_commands.Range[int, 1, 31] | None = None,
        channel: discord.TextChannel | None = None,
        template: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            current = await self._birthday_service.get_recurring_celebration(
                interaction.guild.id, event_id
            )
            target_enabled = current.enabled if enabled is None else enabled
            target_channel_id = channel.id if channel is not None else current.channel_id
            if target_enabled:
                await _require_ready_delivery(
                    self._settings_service,
                    interaction.guild,
                    kind="recurring_event",
                    channel_id=target_channel_id,
                )
            updated = await self._birthday_service.upsert_recurring_celebration(
                guild_id=interaction.guild.id,
                celebration_id=event_id,
                name=name or current.name,
                month=month or current.event_month,
                day=day or current.event_day,
                channel_id=channel.id if channel is not None else current.channel_id,
                template=template if template is not None else current.template,
                enabled=current.enabled if enabled is None else enabled,
            )
        except ContentPolicyError as exc:
            await _audit_blocked_attempt(
                self._studio_audit_logger,
                interaction,
                surface="recurring_event_edit",
                error=exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except (ValidationError, NotFoundError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"Recurring event `{updated.name}` updated.",
            ephemeral=True,
        )

    @event.command(
        name="list",
        description="List recurring annual celebrations for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        celebrations = await self._birthday_service.list_recurring_celebrations(
            interaction.guild.id
        )
        if not celebrations:
            await interaction.followup.send(
                "No recurring events are configured yet.", ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=_build_recurring_event_list_embed(celebrations),
            ephemeral=True,
        )

    @event.command(name="remove", description="Remove a recurring annual celebration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def event_remove(self, interaction: discord.Interaction, event_id: int) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await self._birthday_service.remove_recurring_celebration(
                interaction.guild.id,
                event_id,
            )
        except NotFoundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"Removed recurring event `{deleted.name}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="health",
        description="Run birthday setup and scheduler health checks.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def health(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        issues = await self._health_service.inspect_guild(interaction.guild)
        await interaction.followup.send(
            embed=_build_health_embed(issues),
            ephemeral=True,
        )

    @app_commands.command(
        name="privacy",
        description="Explain what birthday data is stored and where.",
    )
    @app_commands.guild_only()
    async def privacy(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_build_privacy_embed(),
            ephemeral=True,
        )


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


async def _audit_blocked_attempt(
    audit_logger: StudioAuditLogger,
    interaction: discord.Interaction,
    *,
    surface: str,
    error: ContentPolicyError,
) -> None:
    await audit_logger.log_blocked_attempt(
        interaction,
        surface=surface,
        error=error,
    )


def _build_dry_run_status_embed(
    readiness: object,
    settings: GuildSettings,
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    channel_id: int | None,
    preview_member_count: int,
    preview_error: str | None = None,
) -> discord.Embed:
    from bdayblaze.domain.models import AnnouncementDeliveryReadiness

    assert isinstance(readiness, AnnouncementDeliveryReadiness)
    media_diagnostics = build_presentation_diagnostics(settings.presentation_for_kind(kind))
    embed = BudgetedEmbed.create(
        title="🧪 Dry-Run Preview",
        description="Preview only. No live celebration was sent.",
        color=discord.Color.green() if readiness.status == "ready" else discord.Color.orange(),
    )
    embed.add_field(
        name="Preview surface",
        value=_preview_kind_label(kind),
        inline=False,
    )
    embed.add_field(
        name="Live delivery readiness",
        value=readiness.summary,
        inline=False,
    )
    if readiness.details:
        embed.add_line_fields("Details", readiness.details, inline=False)
    embed.add_line_fields(
        "Routing and mentions",
        (
            f"Live route: {_preview_route_for_kind(settings, kind=kind, channel_id=channel_id)}",
            _preview_mention_status(
                kind=kind,
                preview_member_count=preview_member_count,
                threshold=settings.mention_suppression_threshold,
            ),
        ),
        inline=False,
    )
    embed.add_line_fields(
        "Media and visuals",
        _preview_visual_lines(
            settings,
            kind=kind,
            media_diagnostics=media_diagnostics,
        ),
        inline=False,
    )
    if media_diagnostics:
        embed.add_line_fields(
            "Media diagnostics",
            [diagnostic.detail_line() for diagnostic in media_diagnostics],
            inline=False,
        )
    if preview_error:
        embed.add_field(name="Preview blocked", value=preview_error, inline=False)
    return embed.build()


def _build_health_embed(issues: object) -> discord.Embed:
    from bdayblaze.domain.models import HealthIssue

    assert isinstance(issues, list)
    if not issues:
        return BudgetedEmbed.create(
            title="🩺 Health Check",
            description="No actionable issues were detected.",
            color=discord.Color.green(),
        ).build()

    typed_issues = [issue for issue in issues if isinstance(issue, HealthIssue)]
    embed = BudgetedEmbed.create(
        title="🩺 Health Check",
        color=discord.Color.orange(),
    )
    embed.add_line_fields(
        "Actionable issues",
        [
            f"[{issue.severity.upper()}] `{issue.code}`: {issue.summary}\n"
            f"Action: {issue.action}"
            for issue in typed_issues
        ],
        inline=False,
    )
    return embed.build()


def _build_recurring_event_list_embed(
    celebrations: object,
) -> discord.Embed:
    from bdayblaze.domain.models import RecurringCelebration

    assert isinstance(celebrations, list)
    typed_celebrations = [
        celebration
        for celebration in celebrations
        if isinstance(celebration, RecurringCelebration)
    ]
    embed = BudgetedEmbed.create(
        title="📅 Recurring Events",
        color=discord.Color.blurple(),
    )
    embed.add_line_fields(
        "Configured yearly events",
        [
            (
                f"`{celebration.id}` {celebration.name} • "
                f"{celebration.event_month:02d}/{celebration.event_day:02d} • "
                f"{'Enabled' if celebration.enabled else 'Disabled'} • "
                (
                    f"<#{celebration.channel_id}>"
                    if celebration.channel_id is not None
                    else "Main announcement channel"
                )
            )
            for celebration in typed_celebrations
        ],
        inline=False,
    )
    embed.add_field(
        name="Preview path",
        value=(
            "Use `/birthday test-message` with `kind: recurring_event` and the event id "
            "to dry-run the current saved render."
        ),
        inline=False,
    )
    return embed.build()


def _build_privacy_embed() -> discord.Embed:
    embed = BudgetedEmbed.create(
        title="🔒 Privacy",
        description=(
            "Bdayblaze stores birthdays per server membership, not across servers. "
            "Only month/day, an optional birth year, an optional timezone override, "
            "and a server-scoped visibility setting are stored."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Visibility",
        value=(
            "`private` means only you and admins can view the saved record.\n"
            "`server_visible` allows that member to appear in browse commands for this server."
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
            "Logs avoid raw birth dates, birth years, and raw message bodies with personal data."
        ),
        inline=False,
    )
    return embed.build()


def _build_birthday_embed(
    *,
    title: str,
    description: str,
    birthday: MemberBirthday,
    settings: GuildSettings,
) -> discord.Embed:
    embed = BudgetedEmbed.create(
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
        name="Visibility",
        value="Visible in server browse commands"
        if birthday.profile_visibility == "server_visible"
        else "Private to self + admins",
        inline=True,
    )
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
    return embed.build()


def _build_import_preview_embed(preview: object, applied: bool = False) -> discord.Embed:
    from bdayblaze.domain.models import BirthdayImportPreview

    assert isinstance(preview, BirthdayImportPreview)
    embed = BudgetedEmbed.create(
        title="📥 Birthday Import Preview" if not applied else "✅ Birthday Import Applied",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Rows", value=str(preview.total_rows), inline=True)
    embed.add_field(name="Valid rows", value=str(len(preview.valid_rows)), inline=True)
    embed.add_field(name="Errors", value=str(len(preview.errors)), inline=True)
    if preview.errors:
        embed.add_line_fields(
            "Validation errors",
            [f"Row {error.row_number}: {error.message}" for error in preview.errors[:8]],
            inline=False,
        )
    if not applied:
        embed.add_field(
            name="Apply token",
            value=(
                f"`{preview.apply_token}`\n"
                "Re-run `/birthday import` with the same CSV and this token "
                "to apply the valid rows."
            ),
            inline=False,
        )
    return embed.build()


def _build_preview_unavailable_embed(kind_label: str, reason: str) -> discord.Embed:
    embed = BudgetedEmbed.create(
        title="Preview unavailable",
        description=reason,
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="What to do next",
        value=(
            f"Fix `{kind_label}` settings, rerun preview, and only then trust live readiness."
        ),
        inline=False,
    )
    return embed.build()


async def _build_preview_embed(
    guild: discord.Guild,
    settings: GuildSettings,
    birthday_service: BirthdayService,
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    member: discord.Member | None,
    event_id: int | None,
) -> discord.Embed:
    if kind == "recurring_event":
        if event_id is None:
            raise ValidationError("Recurring-event previews need an event id.")
        celebration = await birthday_service.get_recurring_celebration(guild.id, event_id)
        ensure_safe_announcement_inputs(
            template=celebration.template,
            template_label="Recurring event template",
            title_override=settings.announcement_title_override,
            footer_text=settings.announcement_footer_text,
            event_name=celebration.name,
        )
        return build_announcement_message(
            kind="recurring_event",
            server_name=guild.name,
            recipients=[],
            celebration_mode=settings.celebration_mode,
            announcement_theme=settings.announcement_theme,
            presentation=settings.presentation_for_kind("recurring_event"),
            template=celebration.template,
            preview_label="Preview only - recurring event",
            event_name=celebration.name,
            event_month=celebration.event_month,
            event_day=celebration.event_day,
        ).embed
    if kind == "server_anniversary":
        server_anniversary = await birthday_service.get_server_anniversary(guild.id)
        if server_anniversary is None:
            if guild.created_at is None:
                raise ValidationError(
                    "Discord did not provide the guild creation date. "
                    "Save a custom server-anniversary date first."
                )
            event_month = guild.created_at.month
            event_day = guild.created_at.day
            template = None
        else:
            event_month = server_anniversary.event_month
            event_day = server_anniversary.event_day
            template = server_anniversary.template
        ensure_safe_announcement_inputs(
            template=template,
            template_label="Server anniversary template",
            title_override=settings.announcement_title_override,
            footer_text=settings.announcement_footer_text,
            event_name="Server anniversary",
        )
        return build_announcement_message(
            kind="server_anniversary",
            server_name=guild.name,
            recipients=[],
            celebration_mode=settings.celebration_mode,
            announcement_theme=settings.announcement_theme,
            presentation=settings.presentation_for_kind("server_anniversary"),
            template=template,
            preview_label="Preview only - server anniversary",
            event_name="Server anniversary",
            event_month=event_month,
            event_day=event_day,
        ).embed

    if kind in {"birthday_announcement", "birthday_dm"} and member is not None:
        birthday = await birthday_service.require_birthday(
            guild.id,
            member.id,
            missing_message=(
                f"{member.display_name} does not have a saved birthday in this server."
            ),
        )
        recipients = [
            AnnouncementRenderRecipient(
                mention=member.mention,
                display_name=member.display_name,
                username=member.name,
                birth_month=birthday.birth_month,
                birth_day=birthday.birth_day,
                timezone=birthday.effective_timezone(settings),
            )
        ]
    else:
        recipients = preview_context_for_kind(kind).recipients

    if kind == "anniversary":
        preview = preview_context_for_kind("anniversary")
        preview_recipients = preview.recipients
        if member is not None and member.joined_at is not None:
            preview_recipients = [
                AnnouncementRenderRecipient(
                    mention=member.mention,
                    display_name=member.display_name,
                    username=member.name,
                    anniversary_years=anniversary_years(
                        member.joined_at,
                        now_utc=datetime.now(UTC),
                    ),
                )
            ]
        ensure_safe_announcement_inputs(
            template=settings.anniversary_template,
            template_label="Anniversary template",
            title_override=settings.announcement_title_override,
            footer_text=settings.announcement_footer_text,
            event_name=preview.event_name,
            event_name_label="Anniversary event name",
        )
        return build_announcement_message(
            kind="anniversary",
            server_name=guild.name,
            recipients=preview_recipients,
            celebration_mode=settings.celebration_mode,
            announcement_theme=settings.announcement_theme,
            presentation=settings.presentation_for_kind("anniversary"),
            template=settings.anniversary_template,
            preview_label="Preview only - anniversary",
            event_name=preview.event_name,
            event_month=preview.event_month,
            event_day=preview.event_day,
        ).embed

    ensure_safe_announcement_inputs(
        template=(
            settings.announcement_template
            if kind == "birthday_announcement"
            else settings.birthday_dm_template
        ),
        template_label=(
            "Birthday announcement template"
            if kind == "birthday_announcement"
            else "Birthday DM template"
        ),
        title_override=(
            settings.announcement_title_override if kind != "birthday_dm" else None
        ),
        footer_text=(
            settings.announcement_footer_text if kind != "birthday_dm" else None
        ),
    )
    return build_announcement_message(
        kind=kind,
        server_name=guild.name,
        recipients=recipients,
        celebration_mode=settings.celebration_mode,
        announcement_theme=settings.announcement_theme,
        presentation=settings.presentation_for_kind(kind),
        template=(
            settings.announcement_template
            if kind == "birthday_announcement"
            else settings.birthday_dm_template
        ),
        preview_label="Preview only",
    ).embed


async def _build_validated_import_preview(
    guild: discord.Guild,
    birthday_service: BirthdayService,
    csv_text: str,
) -> tuple[object, set[int]]:
    preview = await birthday_service.preview_birthdays_import(guild.id, csv_text)
    allowed_user_ids = {
        user_id
        for user_id, _ in await resolve_guild_members(
            guild,
            (row.user_id for row in preview.valid_rows),
        )
    }
    validated_preview = await birthday_service.preview_birthdays_import(
        guild.id,
        csv_text,
        allowed_user_ids=allowed_user_ids,
    )
    return validated_preview, allowed_user_ids


def _preview_kind_label(
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
) -> str:
    return {
        "birthday_announcement": "🎂 Birthday announcement",
        "birthday_dm": "💌 Birthday DM",
        "anniversary": "🎉 Member anniversary",
        "server_anniversary": "🏰 Server anniversary",
        "recurring_event": "📅 Recurring annual event",
    }[kind]


def _preview_route_for_kind(
    settings: GuildSettings,
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    channel_id: int | None,
) -> str:
    if kind == "birthday_dm":
        return "Private DM"
    if kind == "anniversary":
        target_channel_id = (
            channel_id
            or settings.anniversary_channel_id
            or settings.announcement_channel_id
        )
    else:
        target_channel_id = channel_id or settings.announcement_channel_id
    return f"<#{target_channel_id}>" if target_channel_id is not None else "Not configured"


def _preview_member_count(
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    member: discord.Member | None,
) -> int:
    if kind in {"server_anniversary", "recurring_event"}:
        return 0
    if member is not None:
        return 1
    return len(preview_context_for_kind(kind).recipients)


def _preview_mention_status(
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    preview_member_count: int,
    threshold: int,
) -> str:
    if kind == "birthday_dm":
        return "Mentions: not used in private DMs."
    if kind in {"server_anniversary", "recurring_event"}:
        return "Mentions: not used for this celebration type."
    if preview_member_count >= threshold:
        return "Mentions: would be suppressed for a batch this size."
    return "Mentions: would be allowed for a small live batch."


def _preview_visual_lines(
    settings: GuildSettings,
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    media_diagnostics: tuple[object, ...],
) -> tuple[str, ...]:
    if kind == "birthday_dm":
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
        _preview_media_state_line(settings.announcement_image_url, label="Image"),
        _preview_media_state_line(settings.announcement_thumbnail_url, label="Thumbnail"),
    )


def _preview_media_state_line(value: str | None, *, label: str) -> str:
    assessment = assess_media_url(value, label=label)
    if assessment is None:
        return f"{label}: Not set"
    return f"{label}: {assessment.status_label()}"


async def _require_ready_delivery(
    settings_service: SettingsService,
    guild: discord.Guild,
    *,
    kind: Literal[
        "birthday_announcement",
        "birthday_dm",
        "anniversary",
        "server_anniversary",
        "recurring_event",
    ],
    channel_id: int | None,
) -> None:
    readiness = await settings_service.describe_delivery(
        guild,
        kind=kind,
        channel_id=channel_id,
    )
    if readiness.status == "ready":
        return
    raise ValidationError(readiness.details[0] if readiness.details else readiness.summary)


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


def _format_wish_link_line(link_url: str) -> str:
    return f"Link: {_format_wish_link_value(link_url)}"


def _format_wish_link_value(link_url: str) -> str:
    assessment = assess_media_url(link_url, label="Wish link")
    display_url = truncate_text(link_url, 120)
    if assessment is None:
        return display_url
    labels = {
        "direct_media": "Likely direct media",
        "webpage": "Safe webpage link",
        "unsupported_media": "Safe link, unsupported inline media",
        "needs_validation": "Safe link, media type unconfirmed",
        "invalid_or_unsafe": "Stored link needs review",
    }
    return (
        f"{labels.get(assessment.classification, 'Link')}: {display_url}\n"
        f"Note: {_short_wish_link_summary(assessment.summary)}"
    )


def _short_wish_link_summary(summary: str) -> str:
    prefix = "Wish link URL "
    if summary.startswith(prefix):
        return summary[len(prefix) :]
    return summary


def _build_wish_list_embed(
    wishes: list[BirthdayWish],
    members_by_id: dict[int, discord.Member],
) -> discord.Embed:
    embed = BudgetedEmbed.create(
        title="Queued birthday wishes",
        description="These wishes stay private until the target member's birthday.",
        color=discord.Color.blurple(),
    )
    lines: list[str] = []
    for wish in wishes[:10]:
        target = members_by_id.get(wish.target_user_id)
        target_label = target.mention if target is not None else f"`{wish.target_user_id}`"
        line = f"{target_label} - {wish.wish_text}"
        if wish.link_url is not None:
            line = f"{line}\n{_format_wish_link_line(wish.link_url)}"
        lines.append(line)
    embed.add_line_fields("Queued wishes", lines, inline=False)
    if len(wishes) > 10:
        embed.set_footer(f"Showing 10 of {len(wishes)} queued wishes.")
    return embed.build()


def _build_capsule_preview_embed(
    target: discord.abc.User,
    *,
    celebration: object,
    wishes: list[BirthdayWish],
    queued_count: int,
    authors_by_id: dict[int, discord.Member],
    viewer_is_admin: bool,
) -> discord.Embed:
    title = f"{target.display_name}'s Birthday Capsule"
    embed = BudgetedEmbed.create(title=title, color=discord.Color.blurple())
    if celebration is not None and getattr(celebration, "revealed_wish_count", 0) > 0:
        embed.set_description(
            "This capsule is already unlocked for the current birthday celebration."
        )
        lines = []
        for wish in wishes[:12]:
            author = authors_by_id.get(wish.author_user_id)
            author_name = author.display_name if author is not None else "A friend"
            line = f"{author_name} - {wish.wish_text}"
            if wish.link_url is not None:
                line = f"{line}\n{_format_wish_link_line(wish.link_url)}"
            lines.append(line)
        embed.add_line_fields(
            "Unlocked wishes",
            lines or ["No unlocked wishes found."],
            inline=False,
        )
        if len(wishes) > 12:
            embed.set_footer(f"Showing 12 of {len(wishes)} unlocked wishes.")
        return embed.build()
    if viewer_is_admin:
        embed.set_description("Admin preview of queued wishes before reveal.")
        lines = []
        for wish in wishes[:10]:
            author = authors_by_id.get(wish.author_user_id)
            author_name = author.display_name if author is not None else f"`{wish.author_user_id}`"
            line = f"{author_name} - {wish.wish_text}"
            if wish.link_url is not None:
                line = f"{line}\n{_format_wish_link_line(wish.link_url)}"
            lines.append(line)
        embed.add_line_fields(
            "Queued wishes",
            lines or ["No queued wishes are waiting for this member."],
            inline=False,
        )
        if queued_count > 10:
            embed.set_footer(f"Showing 10 of {queued_count} queued wishes.")
        return embed.build()
    embed.set_description(
        f"{queued_count} wish(es) are queued for this capsule.\n"
        "Queued wish contents stay private until the birthday unlocks."
    )
    return embed.build()


def _build_quest_status_embed(
    status: BirthdayQuestStatus,
    *,
    celebration_override: BirthdayCelebration | None = None,
) -> discord.Embed:
    celebration = celebration_override if celebration_override is not None else status.celebration
    settings = status.settings
    embed = BudgetedEmbed.create(
        title="Birthday Quest",
        description="A compact birthday challenge that only tracks safe, low-noise milestones.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Quest status",
        value="Enabled" if settings.quests_enabled else "Disabled",
        inline=True,
    )
    embed.add_field(
        name="Wish target",
        value=str(settings.quest_wish_target),
        inline=True,
    )
    embed.add_field(
        name="Reaction target",
        value=str(settings.quest_reaction_target),
        inline=True,
    )
    embed.add_field(
        name="Check-in",
        value="Required" if settings.quest_checkin_enabled else "Not required",
        inline=True,
    )
    if celebration is None:
        embed.add_field(
            name="Current birthday",
            value="No active birthday celebration is open for quest progress right now.",
            inline=False,
        )
        return embed.build()
    embed.add_line_fields(
        "Current progress",
        (
            (
                f"\U0001F381 Wishes unlocked: "
                f"{celebration.revealed_wish_count}/{celebration.quest_wish_target}"
            ),
            (
                f"\U0001F389 Reactions on birthday post: "
                f"{celebration.quest_reaction_count}/{celebration.quest_reaction_target}"
                if celebration.quest_reaction_target > 0
                else (
                    "\U0001F389 Reactions on birthday post: "
                    "not required for this celebration"
                )
            ),
            (
                "\U0001F4CD Check-in complete"
                if celebration.quest_checked_in_at_utc is not None
                else "\U0001F4CD Check in with `/birthday quest check-in`"
                if celebration.quest_checkin_required
                else "\U0001F4CD No check-in needed"
            ),
            (
                f"\U0001F3C1 Quest badge earned: "
                f"{'Yes' if celebration.quest_completed_at_utc else 'No'}"
            ),
        ),
        inline=False,
    )
    if celebration.late_delivery:
        embed.add_field(
            name="Recovery note",
            value=LATE_CELEBRATION_NOTE,
            inline=False,
        )
    return embed.build()


def _build_timeline_embed(
    target: discord.abc.User,
    timeline: BirthdayTimeline,
    *,
    active_now: bool,
) -> discord.Embed:
    description = (
        "Celebration is live today."
        if active_now
        else f"Next birthday: {discord.utils.format_dt(timeline.next_countdown_at_utc, 'R')}"
    )
    embed = BudgetedEmbed.create(
        title=f"{target.display_name}'s Birthday Timeline",
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_line_fields(
        "Profile",
        (
            f"Date: {timeline.birthday.birth_month:02d}/{timeline.birthday.birth_day:02d}",
            (
                "Visibility: Visible in server browsing"
                if timeline.birthday.profile_visibility == "server_visible"
                else "Visibility: Private to self and admins"
            ),
            f"Celebrations: {timeline.celebration_count}",
            f"Streak: {timeline.celebration_streak}",
        ),
        inline=False,
    )
    active = timeline.active_celebration
    if active is not None:
        current_lines = [
            "\U0001F382 Status: celebrating right now"
            if active_now
            else (
                f"\U0001F382 Countdown: "
                f"{discord.utils.format_dt(timeline.next_countdown_at_utc, 'R')}"
            ),
            f"\U0001F4E6 Capsule: {_capsule_state_label(active)}",
            (
                f"\U0001F381 Quest wishes: "
                f"{active.revealed_wish_count}/{active.quest_wish_target}"
                if active.quest_enabled
                else "\U0001F381 Quest wishes: quest disabled for this celebration"
            ),
        ]
        if active.quest_enabled:
            current_lines.append(
                f"\U0001F389 Quest reactions: "
                f"{active.quest_reaction_count}/{active.quest_reaction_target}"
                if active.quest_reaction_target > 0
                else "\U0001F389 Quest reactions: public reaction goal unavailable"
            )
            current_lines.append(
                "\U0001F4CD Check-in: complete"
                if active.quest_checked_in_at_utc is not None
                else "\U0001F4CD Check-in: required"
                if active.quest_checkin_required
                else "\U0001F4CD Check-in: not required"
            )
            current_lines.append(
                "\U0001F3C1 Quest badge: earned"
                if active.quest_completed_at_utc is not None
                else "\U0001F3C1 Quest badge: in progress"
            )
        if active.surprise_reward_label is not None:
            current_lines.append(f"\U0001F381 Birthday Surprise: {active.surprise_reward_label}")
        if active.nitro_fulfillment_status == "pending":
            current_lines.append(
                "\U0001F6E1 Nitro concierge: awaiting manual admin fulfillment"
            )
        elif active.nitro_fulfillment_status is not None:
            current_lines.append(
                "\U0001F6E1 Nitro concierge: "
                f"{active.nitro_fulfillment_status.replace('_', ' ')}"
            )
        if active.late_delivery:
            current_lines.append(f"\u23F0 {LATE_CELEBRATION_NOTE}")
        embed.add_line_fields("Current celebration", tuple(current_lines), inline=False)
    extras = [
        f"Wishes received: {timeline.wishes_received_count}",
        f"Quest badges: {timeline.quest_badge_count}",
        f"Surprises earned: {timeline.surprise_count}",
        f"Featured birthdays: {timeline.featured_count}",
        f"Same-day visible birthdays: {timeline.same_day_count}",
        f"Visible birthdays this month: {timeline.month_total_count}",
    ]
    if timeline.zodiac_label is not None:
        extras.append(f"Zodiac: {timeline.zodiac_label}")
    embed.add_line_fields("Highlights", tuple(extras), inline=False)
    if timeline.entries:
        lines = []
        for entry in timeline.entries:
            line = discord.utils.format_dt(entry.occurrence_start_at_utc, "D")
            notes = []
            if entry.late_delivery:
                notes.append("Late recovery")
            if entry.revealed_wish_count:
                notes.append(f"{entry.revealed_wish_count} wishes")
            if entry.quest_completed:
                notes.append("Quest badge")
            if entry.featured_birthday:
                notes.append("Featured")
            if entry.surprise_reward_label is not None:
                notes.append(entry.surprise_reward_label)
            if entry.nitro_fulfillment_status is not None:
                notes.append(f"Nitro: {entry.nitro_fulfillment_status}")
            if notes:
                line = f"{line} - {', '.join(notes)}"
            lines.append(line)
        embed.add_line_fields("Recent celebrations", lines, inline=False)
    return embed.build()


def _build_analytics_embed(analytics: GuildAnalytics) -> discord.Embed:
    most_active_month = (
        f"{month_name[analytics.most_active_month]} ({analytics.most_active_month_count})"
        if analytics.most_active_month
        else "None"
    )
    embed = BudgetedEmbed.create(
        title="Bdayblaze analytics",
        description="Compact server analytics from stored celebration data only.",
        color=discord.Color.blurple(),
    )
    embed.add_line_fields(
        "Birthdays",
        (
            f"Stored birthdays: {analytics.birthdays_total}",
            f"Visible: {analytics.birthdays_visible}",
            f"Private: {analytics.birthdays_private}",
            f"Most active month: {most_active_month}",
        ),
        inline=False,
    )
    embed.add_line_fields(
        "Experience",
        (
            f"Wishes queued: {analytics.wishes_queued}",
            f"Wishes revealed: {analytics.wishes_revealed}",
            f"Quest completions: {analytics.quest_completions}",
            f"Surprises triggered: {analytics.surprises_total}",
        ),
        inline=False,
    )
    embed.add_line_fields(
        "Manual fulfillment",
        (
            f"Nitro pending: {analytics.nitro_pending}",
            f"Nitro delivered: {analytics.nitro_delivered}",
            f"Nitro not delivered: {analytics.nitro_not_delivered}",
        ),
        inline=False,
    )
    embed.add_line_fields(
        "Operations",
        (
            f"Tracked anniversaries: {analytics.anniversaries_tracked}",
            f"Recurring events: {analytics.recurring_events_total}",
            f"Recent late recoveries: {analytics.recent_late_recoveries}",
            f"Recent scheduler issues: {analytics.recent_scheduler_issues}",
        ),
        inline=False,
    )
    return embed.build()


def _build_nitro_queue_embed(
    entries: list[NitroConciergeEntry],
    members_by_id: dict[int, discord.Member],
) -> discord.Embed:
    embed = BudgetedEmbed.create(
        title="Nitro concierge queue",
        description="Manual-only fulfillment records. The bot never buys or sends Nitro.",
        color=discord.Color.blurple(),
    )
    if not entries:
        embed.add_field(
            name="Queue",
            value="No pending Nitro concierge fulfillments are waiting right now.",
            inline=False,
        )
        return embed.build()
    lines = []
    for entry in entries[:10]:
        member = members_by_id.get(entry.user_id)
        member_label = member.mention if member is not None else f"`{entry.user_id}`"
        line = (
            f"`{entry.celebration_id}` - {member_label} - "
            f"{discord.utils.format_dt(entry.occurrence_start_at_utc, 'D')} - {entry.reward_label}"
        )
        if entry.note_text is not None:
            line = f"{line}\nNote: {entry.note_text}"
        lines.append(line)
    embed.add_line_fields("Pending records", lines, inline=False)
    if len(entries) > 10:
        embed.set_footer(f"Showing 10 of {len(entries)} pending Nitro records.")
    return embed.build()


def _capsule_state_label(celebration: BirthdayCelebration) -> str:
    return {
        "disabled": "Capsules disabled",
        "no_wishes": "No wishes queued yet",
        "revealed_private": "Unlocked privately in timeline/admin preview",
        "pending_public": "Ready for public reveal",
        "posted_public": "Posted publicly",
    }.get(celebration.capsule_state, celebration.capsule_state.replace("_", " "))


async def _refresh_live_quest_progress(
    interaction: discord.Interaction,
    celebration: BirthdayCelebration | None,
) -> bool:
    if (
        interaction.guild is None
        or celebration is None
        or celebration.announcement_message_id is None
        or celebration.quest_reaction_target <= 0
    ):
        return False
    refresher = getattr(interaction.client, "refresh_birthday_reactions_for_message", None)
    if not callable(refresher):
        return False
    return bool(
        await refresher(
            guild_id=interaction.guild.id,
            message_id=celebration.announcement_message_id,
        )
    )


def _timeline_is_active_now(timeline: BirthdayTimeline, settings: GuildSettings) -> bool:
    return is_birthday_active_now(
        birth_month=timeline.birthday.birth_month,
        birth_day=timeline.birthday.birth_day,
        timezone_name=timeline.birthday.effective_timezone(settings),
        now_utc=datetime.now(UTC),
    )


def _is_manage_guild(interaction: discord.Interaction) -> bool:
    return _user_is_admin(interaction)


def _current_month(default_timezone: str) -> int:
    try:
        return datetime.now(UTC).astimezone(ZoneInfo(default_timezone)).month
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).month


def _visible_only_for_scope(
    interaction: discord.Interaction,
    scope: Literal["visible", "all"],
) -> bool:
    if scope == "all" and not _user_is_admin(interaction):
        raise ValidationError("Only admins can browse private birthday entries.")
    return scope != "all"


def _user_is_admin(interaction: discord.Interaction) -> bool:
    permissions = interaction.permissions
    return permissions.manage_guild if permissions is not None else False


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
