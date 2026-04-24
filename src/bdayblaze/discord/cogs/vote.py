from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.discord.ui.vote import (
    build_owner_vote_status_text,
    build_vote_embed,
)
from bdayblaze.services.vote_service import VoteService


class VoteCog(commands.Cog):
    def __init__(self, vote_service: VoteService) -> None:
        super().__init__()
        self._vote_service = vote_service

    @app_commands.command(
        name="vote",
        description="Check your Top.gg vote bonus and open the vote page.",
    )
    async def vote(self, interaction: discord.Interaction) -> None:
        status = await self._vote_service.get_vote_bonus_status(interaction.user.id)
        await interaction.response.send_message(
            embed=build_vote_embed(status),
            view=_VoteStatusView(
                vote_service=self._vote_service,
                owner_id=interaction.user.id,
                status=status,
                vote_url=self._vote_service.vote_url,
            ),
            ephemeral=True,
        )

    @commands.command(name="topggvoteadmin", hidden=True)
    async def topggvoteadmin(
        self,
        ctx: commands.Context[Any],
        action: str = "status",
        scope: str | None = None,
        user_id: int | None = None,
    ) -> None:
        if ctx.guild is not None:
            await ctx.send("Use this command in DM only.")
            return
        if not await ctx.bot.is_owner(ctx.author):
            await ctx.send("You are not allowed to use this command.")
            return
        if action != "status":
            await ctx.send("Use `status` or `status user <discord_user_id>`.")
            return
        if scope == "user" and user_id is None:
            await ctx.send("Use `status user <discord_user_id>` for per-user diagnostics.")
            return
        diagnostics = self._vote_service.diagnostics_snapshot()
        if scope == "user" and user_id is not None:
            status = await self._vote_service.get_vote_bonus_status(user_id)
            receipts = await self._vote_service.list_recent_vote_receipts(user_id, limit=5)
            await ctx.send(
                build_owner_vote_status_text(
                    diagnostics=diagnostics,
                    status=status,
                    discord_user_id=user_id,
                    receipts=receipts,
                )
            )
            return
        await ctx.send(
            build_owner_vote_status_text(
                diagnostics=diagnostics,
                status=None,
            )
        )


class _VoteStatusView(discord.ui.View):
    def __init__(
        self,
        *,
        vote_service: VoteService,
        owner_id: int,
        status: object,
        vote_url: str,
    ) -> None:
        super().__init__(timeout=300)
        self._vote_service = vote_service
        self._owner_id = owner_id
        self.add_item(
            discord.ui.Button(
                label="Vote on Top.gg",
                style=discord.ButtonStyle.link,
                url=vote_url,
            )
        )
        if getattr(status, "refresh_available", False):
            self.add_item(
                _RefreshVoteButton(
                    vote_service=vote_service,
                    owner_id=owner_id,
                    vote_url=vote_url,
                    disabled=getattr(status, "refresh_retry_after_seconds", None) is not None,
                    label=(
                        f"Refresh ({getattr(status, 'refresh_retry_after_seconds')}s)"
                        if getattr(status, "refresh_retry_after_seconds", None) is not None
                        else "Refresh"
                    ),
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message("This vote panel belongs to someone else.", ephemeral=True)
            return False
        return True


class _RefreshVoteButton(discord.ui.Button[_VoteStatusView]):
    def __init__(
        self,
        *,
        vote_service: VoteService,
        owner_id: int,
        vote_url: str,
        disabled: bool,
        label: str,
    ) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
        )
        self._vote_service = vote_service
        self._owner_id = owner_id
        self._vote_url = vote_url

    async def callback(self, interaction: discord.Interaction) -> None:
        result = await self._vote_service.refresh_vote_status(interaction.user.id)
        await interaction.response.edit_message(
            embed=build_vote_embed(result.status, notice=result.note),
            view=_VoteStatusView(
                vote_service=self._vote_service,
                owner_id=self._owner_id,
                status=result.status,
                vote_url=self._vote_url,
            ),
        )
