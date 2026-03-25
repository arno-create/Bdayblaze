from __future__ import annotations

import asyncio
from time import monotonic

import discord
from discord import app_commands
from discord.ext import commands

from bdayblaze.container import ServiceContainer
from bdayblaze.discord.cogs.birthday import BirthdayGroup
from bdayblaze.discord.cogs.info import InfoCog
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.diagnostics import classify_discord_http_failure
from bdayblaze.services.errors import BdayblazeError


class BdayblazeBot(commands.Bot):
    _REACTION_CACHE_LIMIT = 512
    _REACTION_DEBOUNCE_SECONDS = 1.5
    _REACTION_NEGATIVE_TTL_SECONDS = 60.0
    _REACTION_POSITIVE_TTL_SECONDS = 3600.0

    def __init__(self, container: ServiceContainer) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_reactions = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.container = container
        self._logger = get_logger(component="bot")
        self._scheduler_started = False
        self._reaction_refresh_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        self._reaction_message_cache: dict[tuple[int, int], tuple[bool, float]] = {}
        self._reaction_channel_cache: dict[tuple[int, int], int] = {}

    async def setup_hook(self) -> None:
        self.tree.error(self.on_app_command_error)
        await self.add_cog(
            BirthdayGroup(
                birthday_service=self.container.birthday_service,
                experience_service=self.container.experience_service,
                settings_service=self.container.settings_service,
                health_service=self.container.health_service,
                studio_audit_logger=self.container.studio_audit_logger,
            )
        )
        await self.add_cog(InfoCog())
        if self.container.settings.guild_sync_ids:
            for guild_id in self.container.settings.guild_sync_ids:
                await self.tree.sync(guild=discord.Object(id=guild_id))
        else:
            await self.tree.sync()
        self._logger.info(
            "app_commands_synced",
            guild_sync_count=len(self.container.settings.guild_sync_ids),
        )

    async def on_ready(self) -> None:
        if not self._scheduler_started:
            self.container.scheduler_runner.start()
            self._scheduler_started = True
        self.container.runtime_status.bot_ready_at_utc = discord.utils.utcnow()
        self.container.runtime_status.startup_phase = "bot_ready"
        self._logger.info("bot_ready", user=str(self.user), guild_count=len(self.guilds))

    async def close(self) -> None:
        for task in self._reaction_refresh_tasks.values():
            task.cancel()
        if self._scheduler_started:
            await self.container.scheduler_runner.stop()
        self._logger.info("bot_closing")
        await self.container.pool.close()
        await super().close()

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_raw_reaction_event(payload)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_raw_reaction_event(payload)

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
            failure = classify_discord_http_failure(original, surface="ui")
            self._logger.warning(
                "app_command_http_error",
                command=command_name,
                guild_id=guild_hash,
                user_id=user_hash,
                status=original.status,
                discord_code=original.code,
                error_type=type(original).__name__,
            )
            if failure.permanent:
                message = failure.summary
                if failure.action:
                    message = f"{message}\nAction: {failure.action}"
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

    async def refresh_birthday_reactions_for_message(
        self,
        *,
        guild_id: int,
        message_id: int,
        channel_id: int | None = None,
    ) -> bool:
        key = (guild_id, message_id)
        if not await self._is_tracked_reaction_message(
            guild_id=guild_id,
            message_id=message_id,
            channel_id=channel_id,
            force_lookup=True,
        ):
            return False
        target_channel_id = channel_id or self._reaction_channel_cache.get(key)
        if target_channel_id is None:
            target_channel_id = (
                await self.container.experience_service.fetch_announcement_channel_for_message(
                    guild_id,
                    message_id,
                )
            )
            if target_channel_id is None:
                return False
        self._remember_reaction_message(
            key,
            tracked=True,
            channel_id=target_channel_id,
        )
        await self._refresh_birthday_reaction_count(
            guild_id=guild_id,
            message_id=message_id,
            channel_id=target_channel_id,
        )
        return True

    async def _handle_raw_reaction_event(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.channel_id is None:
            return
        if self.user is not None and payload.user_id == self.user.id:
            return
        guild_id = int(payload.guild_id)
        message_id = int(payload.message_id)
        channel_id = int(payload.channel_id)
        if not await self._is_tracked_reaction_message(
            guild_id=guild_id,
            message_id=message_id,
            channel_id=channel_id,
        ):
            return
        self._schedule_reaction_refresh(
            guild_id=guild_id,
            message_id=message_id,
            channel_id=channel_id,
        )

    async def _is_tracked_reaction_message(
        self,
        *,
        guild_id: int,
        message_id: int,
        channel_id: int | None,
        force_lookup: bool = False,
    ) -> bool:
        key = (guild_id, message_id)
        cached = self._cached_reaction_message(key)
        if cached is not None and not force_lookup:
            if cached and channel_id is not None:
                self._remember_reaction_message(key, tracked=True, channel_id=channel_id)
            return cached
        tracked = await self.container.experience_service.has_tracked_birthday_announcement_message(
            guild_id,
            message_id,
        )
        self._remember_reaction_message(
            key,
            tracked=tracked,
            channel_id=channel_id if tracked else None,
        )
        return tracked

    def _schedule_reaction_refresh(
        self,
        *,
        guild_id: int,
        message_id: int,
        channel_id: int,
    ) -> None:
        key = (guild_id, message_id)
        self._remember_reaction_message(key, tracked=True, channel_id=channel_id)
        existing = self._reaction_refresh_tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()
        self._reaction_refresh_tasks[key] = asyncio.create_task(
            self._debounced_reaction_refresh(
                guild_id=guild_id,
                message_id=message_id,
            )
        )

    async def _debounced_reaction_refresh(
        self,
        *,
        guild_id: int,
        message_id: int,
    ) -> None:
        key = (guild_id, message_id)
        try:
            await asyncio.sleep(self._REACTION_DEBOUNCE_SECONDS)
            channel_id = self._reaction_channel_cache.get(key)
            if channel_id is None:
                channel_id = (
                    await self.container.experience_service.fetch_announcement_channel_for_message(
                        guild_id,
                        message_id,
                    )
                )
                if channel_id is None:
                    return
            await self._refresh_birthday_reaction_count(
                guild_id=guild_id,
                message_id=message_id,
                channel_id=channel_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception(
                "reaction_refresh_failed",
                guild_id=redact_identifier(guild_id),
                message_id=redact_identifier(message_id),
            )
        finally:
            current = self._reaction_refresh_tasks.get(key)
            if current is asyncio.current_task():
                self._reaction_refresh_tasks.pop(key, None)

    async def _refresh_birthday_reaction_count(
        self,
        *,
        guild_id: int,
        message_id: int,
        channel_id: int,
    ) -> None:
        key = (guild_id, message_id)
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound):
                await (
                    self.container.experience_service.disable_birthday_announcement_reaction_tracking(
                        guild_id,
                        message_id,
                    )
                )
                self._remember_reaction_message(key, tracked=False, channel_id=None)
                return
            except discord.HTTPException:
                self._logger.warning(
                    "reaction_channel_fetch_failed",
                    guild_id=redact_identifier(guild_id),
                    channel_id=redact_identifier(channel_id),
                    message_id=redact_identifier(message_id),
                )
                return
        if not hasattr(channel, "fetch_message"):
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound):
            await self.container.experience_service.disable_birthday_announcement_reaction_tracking(
                guild_id,
                message_id,
            )
            self._remember_reaction_message(key, tracked=False, channel_id=None)
            return
        except discord.HTTPException:
            self._logger.warning(
                "reaction_message_fetch_failed",
                guild_id=redact_identifier(guild_id),
                channel_id=redact_identifier(channel_id),
                message_id=redact_identifier(message_id),
            )
            return
        reaction_count = sum(reaction.count for reaction in message.reactions)
        await self.container.experience_service.refresh_birthday_announcement_reactions(
            guild_id,
            message_id,
            reaction_count,
        )
        self._remember_reaction_message(key, tracked=True, channel_id=channel_id)

    def _cached_reaction_message(self, key: tuple[int, int]) -> bool | None:
        cached = self._reaction_message_cache.get(key)
        if cached is None:
            return None
        tracked, expires_at = cached
        if monotonic() >= expires_at:
            self._reaction_message_cache.pop(key, None)
            self._reaction_channel_cache.pop(key, None)
            return None
        return tracked

    def _remember_reaction_message(
        self,
        key: tuple[int, int],
        *,
        tracked: bool,
        channel_id: int | None,
    ) -> None:
        self._reaction_message_cache.pop(key, None)
        self._reaction_message_cache[key] = (
            tracked,
            monotonic()
            + (
                self._REACTION_POSITIVE_TTL_SECONDS
                if tracked
                else self._REACTION_NEGATIVE_TTL_SECONDS
            ),
        )
        while len(self._reaction_message_cache) > self._REACTION_CACHE_LIMIT:
            stale_key = next(iter(self._reaction_message_cache))
            self._reaction_message_cache.pop(stale_key, None)
            self._reaction_channel_cache.pop(stale_key, None)
        if tracked and channel_id is not None:
            self._reaction_channel_cache.pop(key, None)
            self._reaction_channel_cache[key] = channel_id
            while len(self._reaction_channel_cache) > self._REACTION_CACHE_LIMIT:
                self._reaction_channel_cache.pop(next(iter(self._reaction_channel_cache)), None)
        elif not tracked:
            self._reaction_channel_cache.pop(key, None)
