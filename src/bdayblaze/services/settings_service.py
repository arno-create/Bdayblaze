from __future__ import annotations

from dataclasses import replace

import discord

from bdayblaze.domain.announcement_template import validate_announcement_template
from bdayblaze.domain.announcement_theme import validate_announcement_theme
from bdayblaze.domain.birthday_logic import validate_timezone
from bdayblaze.domain.models import (
    AnnouncementDeliveryReadiness,
    AnnouncementTheme,
    CelebrationMode,
    GuildSettings,
)
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.errors import ValidationError


class _UnsetType:
    pass


UNSET = _UnsetType()


class SettingsService:
    def __init__(self, repository: PostgresRepository) -> None:
        self._repository = repository

    async def get_settings(self, guild_id: int) -> GuildSettings:
        stored = await self._repository.fetch_guild_settings(guild_id)
        return stored or GuildSettings.default(guild_id)

    async def update_settings(
        self,
        guild: discord.Guild,
        *,
        announcement_channel_id: int | None | _UnsetType = UNSET,
        default_timezone: str | _UnsetType = UNSET,
        birthday_role_id: int | None | _UnsetType = UNSET,
        announcements_enabled: bool | _UnsetType = UNSET,
        role_enabled: bool | _UnsetType = UNSET,
        celebration_mode: CelebrationMode | _UnsetType = UNSET,
        announcement_theme: AnnouncementTheme | _UnsetType = UNSET,
        announcement_template: str | None | _UnsetType = UNSET,
    ) -> GuildSettings:
        current = await self.get_settings(guild.id)
        merged_announcement_channel_id = (
            current.announcement_channel_id
            if isinstance(announcement_channel_id, _UnsetType)
            else announcement_channel_id
        )
        merged_default_timezone = (
            current.default_timezone
            if isinstance(default_timezone, _UnsetType)
            else default_timezone
        )
        merged_birthday_role_id = (
            current.birthday_role_id
            if isinstance(birthday_role_id, _UnsetType)
            else birthday_role_id
        )
        merged_announcements_enabled = (
            current.announcements_enabled
            if isinstance(announcements_enabled, _UnsetType)
            else announcements_enabled
        )
        merged_role_enabled = (
            current.role_enabled if isinstance(role_enabled, _UnsetType) else role_enabled
        )
        merged_celebration_mode = (
            current.celebration_mode
            if isinstance(celebration_mode, _UnsetType)
            else celebration_mode
        )
        merged_announcement_theme = (
            current.announcement_theme
            if isinstance(announcement_theme, _UnsetType)
            else announcement_theme
        )
        merged_announcement_template = (
            current.announcement_template
            if isinstance(announcement_template, _UnsetType)
            else announcement_template
        )
        try:
            normalized_announcement_theme = validate_announcement_theme(merged_announcement_theme)
            normalized_announcement_template = validate_announcement_template(
                merged_announcement_template
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        merged = replace(
            current,
            announcement_channel_id=merged_announcement_channel_id,
            default_timezone=merged_default_timezone,
            birthday_role_id=merged_birthday_role_id,
            announcements_enabled=merged_announcements_enabled,
            role_enabled=merged_role_enabled,
            celebration_mode=merged_celebration_mode,
            announcement_theme=normalized_announcement_theme,
            announcement_template=normalized_announcement_template,
        )
        self._validate_settings(guild, merged)
        return await self._repository.upsert_guild_settings(merged)

    async def describe_announcement_delivery(
        self,
        guild: discord.Guild,
    ) -> AnnouncementDeliveryReadiness:
        settings = await self.get_settings(guild.id)
        if not settings.announcements_enabled:
            return AnnouncementDeliveryReadiness(
                status="blocked",
                summary="Preview ready. Live delivery is disabled in this server.",
                details=("Announcements are currently disabled.",),
            )

        bot_member = guild.me
        if bot_member is None:
            return AnnouncementDeliveryReadiness(
                status="blocked",
                summary="Preview ready. Live delivery readiness is still loading.",
                details=("Bot member state is not available yet.",),
            )

        if settings.announcement_channel_id is None:
            return AnnouncementDeliveryReadiness(
                status="blocked",
                summary=(
                    "Preview ready. Live delivery is blocked until an announcement channel "
                    "is set."
                ),
                details=("No announcement channel is configured.",),
            )

        channel = guild.get_channel(settings.announcement_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return AnnouncementDeliveryReadiness(
                status="blocked",
                summary=(
                    "Preview ready. Live delivery is blocked by a missing announcement "
                    "channel."
                ),
                details=("The saved announcement channel is missing or invalid.",),
            )

        permissions = channel.permissions_for(bot_member)
        missing_permissions: list[str] = []
        if not permissions.view_channel:
            missing_permissions.append("View Channel")
        if not permissions.send_messages:
            missing_permissions.append("Send Messages")
        if not permissions.embed_links:
            missing_permissions.append("Embed Links")
        if missing_permissions:
            return AnnouncementDeliveryReadiness(
                status="blocked",
                summary=(
                    "Preview ready. Live delivery is blocked by announcement-channel "
                    "permissions."
                ),
                details=(f"Missing permissions: {', '.join(missing_permissions)}.",),
            )

        return AnnouncementDeliveryReadiness(
            status="ready",
            summary="Preview ready. Live delivery is currently ready in this server.",
        )

    @staticmethod
    def _validate_settings(guild: discord.Guild, settings: GuildSettings) -> None:
        try:
            validate_timezone(settings.default_timezone)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        bot_member = guild.me
        if bot_member is None:
            raise ValidationError("Bot member state is unavailable. Try again in a few seconds.")

        if settings.announcement_channel_id is not None:
            channel = guild.get_channel(settings.announcement_channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValidationError(
                    "Announcement channel must be a text or announcement channel."
                )
            permissions = channel.permissions_for(bot_member)
            if (
                not permissions.view_channel
                or not permissions.send_messages
                or not permissions.embed_links
            ):
                raise ValidationError(
                    "The bot needs View Channel, Send Messages, and Embed Links in the "
                    "announcement channel."
                )
        elif settings.announcements_enabled:
            raise ValidationError("Select an announcement channel before enabling announcements.")

        if settings.birthday_role_id is not None:
            role = guild.get_role(settings.birthday_role_id)
            if role is None:
                raise ValidationError("The selected birthday role no longer exists.")
            if role.is_default() or role.managed:
                raise ValidationError(
                    "Choose a dedicated, manually managed role for birthday assignment."
                )
            if not bot_member.guild_permissions.manage_roles:
                raise ValidationError(
                    "The bot needs Manage Roles before a birthday role can be saved."
                )
            if bot_member.top_role <= role:
                raise ValidationError(
                    "Move the bot's highest role above the birthday role before saving."
                )
        elif settings.role_enabled:
            raise ValidationError(
                "Select a dedicated birthday role before enabling role assignment."
            )
