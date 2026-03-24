from __future__ import annotations

from typing import Iterable

import discord

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
        user_ids: list[int],
        celebration_mode: str,
        batch_token: str,
    ) -> AnnouncementSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            raise GatewaySkipError("guild_missing")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise GatewaySkipError("announcement_channel_missing")
        members = await self._resolve_members(guild, user_ids)
        if not members:
            raise GatewaySkipError("members_missing")
        embed = self._build_announcement_embed(members, celebration_mode, batch_token)
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
        members: list[discord.Member] = []
        for user_id in user_ids:
            member = await self._fetch_member(guild, user_id)
            if member is not None:
                members.append(member)
        return members

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
        members: list[discord.Member],
        celebration_mode: str,
        batch_token: str,
    ) -> discord.Embed:
        if len(members) == 1:
            title = "Happy birthday"
            description = f"Celebrating {members[0].mention} today."
        else:
            title = "Birthday crew"
            joined_mentions = ", ".join(member.mention for member in members)
            description = f"Celebrating {joined_mentions} today."
        color = discord.Color.gold() if celebration_mode == "party" else discord.Color.blurple()
        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(
            name="Mode",
            value=(
                "Party mode is enabled for this server."
                if celebration_mode == "party"
                else "Quiet mode keeps celebrations clean and low-noise."
            ),
            inline=False,
        )
        embed.set_footer(text=_batch_footer(batch_token))
        return embed


def _batch_footer(batch_token: str) -> str:
    return f"Bdayblaze | {batch_token}"
