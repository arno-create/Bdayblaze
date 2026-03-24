from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.container import ServiceContainer
from bdayblaze.discord.cogs.birthday import BirthdayGroup
from bdayblaze.discord.cogs.info import InfoCog
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.errors import BdayblazeError


class BdayblazeBot(commands.Bot):
    def __init__(self, container: ServiceContainer) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.container = container
        self._logger = get_logger(component="bot")
        self._scheduler_started = False

    async def setup_hook(self) -> None:
        self.tree.error(self.on_app_command_error)
        await self.add_cog(
            BirthdayGroup(
                birthday_service=self.container.birthday_service,
                settings_service=self.container.settings_service,
                health_service=self.container.health_service,
            )
        )
        await self.add_cog(InfoCog())
        if self.container.settings.guild_sync_ids:
            for guild_id in self.container.settings.guild_sync_ids:
                await self.tree.sync(guild=discord.Object(id=guild_id))
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        if not self._scheduler_started:
            self.container.scheduler_runner.start()
            self._scheduler_started = True
        self._logger.info("bot_ready", user=str(self.user), guild_count=len(self.guilds))

    async def close(self) -> None:
        if self._scheduler_started:
            await self.container.scheduler_runner.stop()
        await self.container.pool.close()
        await super().close()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        guild_hash = (
            redact_identifier(interaction.guild_id) if interaction.guild_id is not None else None
        )
        user_hash = redact_identifier(interaction.user.id)
        is_admin_flow = bool(
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild
        )

        if isinstance(original, app_commands.errors.MissingPermissions):
            message = "You need Manage Server to use that command."
            error_hint: str | None = None
        elif isinstance(original, BdayblazeError):
            message = str(original)
            error_hint = None
        elif isinstance(original, discord.HTTPException):
            self._logger.warning(
                "app_command_http_error",
                command=command_name,
                guild_id=guild_hash,
                user_id=user_hash,
                status=original.status,
                discord_code=original.code,
                error_type=type(original).__name__,
            )
            if original.status == 400:
                message = (
                    "Discord rejected that UI response. Try again after shortening the current "
                    "template or refreshing the panel."
                )
                error_hint = "BDAY-UI-400"
            else:
                message = "Discord rejected that action. Try again in a moment."
                error_hint = f"BDAY-HTTP-{original.status}"
        else:
            self._logger.exception(
                "app_command_error",
                command=command_name,
                guild_id=guild_hash,
                user_id=user_hash,
                error_code=type(original).__name__,
            )
            message = "Something went wrong while handling that command."
            error_hint = "BDAY-UNEXPECTED"

        if is_admin_flow and error_hint is not None:
            message = f"{message}\nHint: `{error_hint}`."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
