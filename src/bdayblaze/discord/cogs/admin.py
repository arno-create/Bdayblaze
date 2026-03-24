from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.discord.ui.setup import SetupView, build_settings_embed
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.settings_service import SettingsService


class BdayblazeGroup(
    commands.GroupCog,
    group_name="bdayblaze",
    group_description="Server setup and diagnostics for Bdayblaze",
):
    def __init__(
        self,
        settings_service: SettingsService,
        health_service: HealthService,
    ) -> None:
        super().__init__()
        self._settings_service = settings_service
        self._health_service = health_service

    @app_commands.command(name="setup", description="Open the interactive server setup panel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_settings_embed(settings),
            view=SetupView(
                settings_service=self._settings_service,
                settings=settings,
                owner_id=interaction.user.id,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="config", description="View the current server configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def config(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await self._settings_service.get_settings(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_settings_embed(settings),
            view=SetupView(
                settings_service=self._settings_service,
                settings=settings,
                owner_id=interaction.user.id,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="health", description="Run configuration and scheduler health checks.")
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

    @app_commands.command(name="privacy", description="Explain what birthday data Bdayblaze stores.")
    @app_commands.guild_only()
    async def privacy(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Privacy",
            description=(
                "Bdayblaze stores birthdays per server membership, not globally across Discord. "
                "Only month/day, optional birth year, and optional timezone override are stored."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Defaults", value="Birth year optional, age hidden, no cross-server sharing.", inline=False)
        embed.add_field(
            name="Deletion",
            value="Use `/birthday remove` to delete your server-scoped birthday data.",
            inline=False,
        )
        embed.add_field(
            name="Operations",
            value="Logs avoid plain-text birth dates and health diagnostics avoid personal data.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
