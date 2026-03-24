from __future__ import annotations

import asyncio
from collections.abc import Iterable

import discord

from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    AnnouncementRenderRecipient,
    render_announcement_template,
)
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
    ) -> int | None:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            async for message in channel.history(limit=25):
                if self._bot.user is None or message.author.id != self._bot.user.id:
                    continue
                for embed in message.embeds:
                    footer = embed.footer.text if embed.footer else None
                    if footer == _batch_footer(batch_token):
                        return message.id
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
        embed = self._build_announcement_embed(
            members=members,
            celebration_mode=celebration_mode,
            batch_token=batch_token,
            template=template,
            server_name=guild.name,
            recipients=render_recipients,
        )
        content = " ".join(member.mention for member in members)
        try:
            message = await channel.send(
                content=content,
                embed=embed,
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

    async def _resolve_members(
        self,
        guild: discord.Guild,
        user_ids: Iterable[int],
    ) -> list[discord.Member]:
        tasks = [self._fetch_member(guild, user_id) for user_id in user_ids]
        resolved = await asyncio.gather(*tasks)
        return [member for member in resolved if member is not None]

    async def _resolve_recipients(
        self,
        guild: discord.Guild,
        recipients: list[AnnouncementRecipientSnapshot],
    ) -> list[tuple[AnnouncementRecipientSnapshot, discord.Member]]:
        members = await self._resolve_members(
            guild, (recipient.user_id for recipient in recipients)
        )
        by_user_id = {member.id: member for member in members}
        return [
            (recipient, member)
            for recipient in recipients
            if (member := by_user_id.get(recipient.user_id)) is not None
        ]

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
        except discord.HTTPException as exc:
            raise GatewayRetryableError("member_lookup_http_error") from exc

    @staticmethod
    def _build_announcement_embed(
        *,
        members: list[discord.Member],
        celebration_mode: str,
        batch_token: str,
        template: str,
        server_name: str,
        recipients: list[AnnouncementRenderRecipient],
    ) -> discord.Embed:
        if len(members) == 1:
            title = "Happy birthday"
        else:
            title = "Birthday crew"
        color = discord.Color.gold() if celebration_mode == "party" else discord.Color.blurple()
        try:
            description = render_announcement_template(
                template,
                server_name=server_name,
                celebration_mode="party" if celebration_mode == "party" else "quiet",
                recipients=recipients,
            )
        except ValueError:
            description = render_announcement_template(
                DEFAULT_ANNOUNCEMENT_TEMPLATE,
                server_name=server_name,
                celebration_mode="party" if celebration_mode == "party" else "quiet",
                recipients=recipients,
            )
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text=_batch_footer(batch_token))
        return embed


def _batch_footer(batch_token: str) -> str:
    return f"Bdayblaze | {batch_token}"
