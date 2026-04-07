from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.discord.ui.info import (
    build_about_embed,
    build_help_embed,
    build_info_links_view,
    build_support_embed,
)


class InfoCog(commands.Cog):
    @app_commands.command(
        name="help",
        description="Show the main Bdayblaze commands and setup flow.",
    )
    async def help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_help_embed(),
            view=build_info_links_view(),
            ephemeral=True,
        )

    @app_commands.command(
        name="about",
        description="Explain what Bdayblaze stores, how it works, and where to get help.",
    )
    async def about(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_about_embed(),
            view=build_info_links_view(),
            ephemeral=True,
        )

    @app_commands.command(
        name="support",
        description="Get support links and the best place to report bugs or issues.",
    )
    async def support(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_support_embed(),
            view=build_info_links_view(),
            ephemeral=True,
        )
