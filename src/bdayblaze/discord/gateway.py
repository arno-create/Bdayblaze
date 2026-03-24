from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord

from bdayblaze.discord.announcements import batch_footer, build_announcement_message
from bdayblaze.discord.member_resolution import MemberResolutionError, resolve_guild_members
from bdayblaze.domain.announcement_template import AnnouncementRenderRecipient
from bdayblaze.domain.models import AnnouncementRecipientSnapshot
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    GatewayRetryableError,
    GatewaySkipError,
)


class DiscordSchedulerGateway:
    def __init__(self, bot: discord.Client) -> None:
        self._bot = bot
        self._logger = get_logger(component="discord_gateway")

    async def find_announcement_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        batch_token: str,
        announcement_theme: str,
        scheduled_for_utc: datetime,
        send_started_at_utc: datetime | None,
    ) -> int | None:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        lower_bound = scheduled_for_utc - timedelta(minutes=15)
        upper_anchor = send_started_at_utc or scheduled_for_utc
        upper_bound = max(upper_anchor, scheduled_for_utc).astimezone(UTC) + timedelta(minutes=15)
        before: datetime | None = upper_bound
        try:
            for _ in range(3):
                history = [
                    message
                    async for message in channel.history(
                        limit=10,
                        before=before,
                        after=lower_bound,
                        oldest_first=False,
                    )
                ]
                if not history:
                    return None
                for message in history:
                    if message.created_at < lower_bound:
                        return None
                    if self._bot.user is None or message.author.id != self._bot.user.id:
                        continue
                    for embed in message.embeds:
                        footer = embed.footer.text if embed.footer else None
                        if footer == batch_footer(announcement_theme, batch_token):
                            return message.id
                before = min(message.created_at for message in history)
        except discord.HTTPException:
            return None
        return None

    async def send_birthday_announcement(
        self,
        *,
        guild_id: int,
        channel_id: int,
        recipients: list[AnnouncementRecipientSnapshot],
        celebration_mode: str,
        announcement_theme: str,
        batch_token: str,
        template: str,
    ) -> AnnouncementSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            raise GatewaySkipError("guild_missing")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise GatewaySkipError("announcement_channel_missing")
        resolved = await self._resolve_recipients(guild, recipients)
        if not resolved:
            raise GatewaySkipError("members_missing")
        members = [member for _, member in resolved]
        render_recipients = [
            AnnouncementRenderRecipient(
                mention=member.mention,
                display_name=member.display_name,
                username=member.name,
                birth_month=snapshot.birth_month,
                birth_day=snapshot.birth_day,
                timezone=snapshot.timezone,
            )
            for snapshot, member in resolved
        ]
        prepared = build_announcement_message(
            server_name=guild.name,
            recipients=render_recipients,
            celebration_mode=celebration_mode,
            announcement_theme=announcement_theme,  # type: ignore[arg-type]
            template=template,
            batch_token=batch_token,
        )
        try:
            message = await channel.send(
                content=prepared.content,
                embed=prepared.embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.Forbidden as exc:
            raise GatewaySkipError("announcement_forbidden") from exc
        except discord.HTTPException as exc:
            raise GatewayRetryableError("announcement_http_error") from exc
        self._logger.info(
            "announcement_sent",
            guild_id=guild_id,
            member_count=len(members),
        )
        return AnnouncementSendResult(message_id=message.id)

    async def add_birthday_role(self, *, guild_id: int, user_id: int, role_id: int) -> str:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return "guild_missing"
        role = guild.get_role(role_id)
        if role is None:
            return "role_missing"
        member = await self._fetch_member(guild, user_id)
        if member is None:
            return "member_missing"
        if role in member.roles:
            return "already_present"
        try:
            await member.add_roles(role, reason="Bdayblaze birthday celebration")
        except discord.Forbidden:
            return "forbidden"
        except discord.HTTPException as exc:
            raise GatewayRetryableError("role_add_http_error") from exc
        self._logger.info(
            "birthday_role_added",
            guild_id=guild_id,
            user_ref=redact_identifier(user_id),
        )
        return "applied"

    async def remove_birthday_role(self, *, guild_id: int, user_id: int, role_id: int) -> str:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return "guild_missing"
        role = guild.get_role(role_id)
        if role is None:
            return "role_missing"
        member = await self._fetch_member(guild, user_id)
        if member is None:
            return "member_missing"
        if role not in member.roles:
            return "already_absent"
        try:
            await member.remove_roles(role, reason="Bdayblaze birthday window ended")
        except discord.Forbidden:
            return "forbidden"
        except discord.HTTPException as exc:
            raise GatewayRetryableError("role_remove_http_error") from exc
        self._logger.info(
            "birthday_role_removed",
            guild_id=guild_id,
            user_ref=redact_identifier(user_id),
        )
        return "applied"

    async def _resolve_recipients(
        self,
        guild: discord.Guild,
        recipients: list[AnnouncementRecipientSnapshot],
    ) -> list[tuple[AnnouncementRecipientSnapshot, discord.Member]]:
        try:
            resolved_members = await resolve_guild_members(
                guild,
                (recipient.user_id for recipient in recipients),
                raise_on_http_error=True,
            )
        except MemberResolutionError as exc:
            raise GatewayRetryableError("member_lookup_http_error") from exc
        by_user_id = {user_id: member for user_id, member in resolved_members}
        return [
            (recipient, member)
            for recipient in recipients
            if (member := by_user_id.get(recipient.user_id)) is not None
        ]

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        try:
            resolved = await resolve_guild_members(
                guild,
                (user_id,),
                raise_on_http_error=True,
            )
        except MemberResolutionError as exc:
            raise GatewayRetryableError("member_lookup_http_error") from exc
        if not resolved:
            return None
        return resolved[0][1]
