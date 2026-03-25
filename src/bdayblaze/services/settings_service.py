from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import discord

from bdayblaze.domain.announcement_template import (
    validate_accent_color,
    validate_announcement_template,
    validate_studio_text,
)
from bdayblaze.domain.announcement_theme import validate_announcement_theme
from bdayblaze.domain.birthday_logic import validate_timezone
from bdayblaze.domain.media_validation import validate_direct_media_url
from bdayblaze.domain.models import (
    AnnouncementDeliveryReadiness,
    AnnouncementKind,
    AnnouncementTheme,
    CelebrationMode,
    GuildSettings,
)
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.content_policy import ensure_safe_template, ensure_safe_text
from bdayblaze.services.diagnostics import (
    build_channel_diagnostics,
    build_presentation_diagnostics,
    build_role_diagnostics,
    describe_anniversary_readiness,
    describe_birthday_announcement_readiness,
    describe_birthday_dm_readiness,
    describe_role_readiness,
)
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
        now_utc: datetime | None = None,
        announcement_channel_id: int | None | _UnsetType = UNSET,
        default_timezone: str | _UnsetType = UNSET,
        birthday_role_id: int | None | _UnsetType = UNSET,
        announcements_enabled: bool | _UnsetType = UNSET,
        role_enabled: bool | _UnsetType = UNSET,
        celebration_mode: CelebrationMode | _UnsetType = UNSET,
        announcement_theme: AnnouncementTheme | _UnsetType = UNSET,
        announcement_template: str | None | _UnsetType = UNSET,
        announcement_title_override: str | None | _UnsetType = UNSET,
        announcement_footer_text: str | None | _UnsetType = UNSET,
        announcement_image_url: str | None | _UnsetType = UNSET,
        announcement_thumbnail_url: str | None | _UnsetType = UNSET,
        announcement_accent_color: str | None | _UnsetType = UNSET,
        birthday_dm_enabled: bool | _UnsetType = UNSET,
        birthday_dm_template: str | None | _UnsetType = UNSET,
        anniversary_enabled: bool | _UnsetType = UNSET,
        anniversary_channel_id: int | None | _UnsetType = UNSET,
        anniversary_template: str | None | _UnsetType = UNSET,
        eligibility_role_id: int | None | _UnsetType = UNSET,
        ignore_bots: bool | _UnsetType = UNSET,
        minimum_membership_days: int | _UnsetType = UNSET,
        mention_suppression_threshold: int | _UnsetType = UNSET,
        studio_audit_channel_id: int | None | _UnsetType = UNSET,
    ) -> GuildSettings:
        current = await self.get_settings(guild.id)
        merged_default_timezone = (
            current.default_timezone
            if isinstance(default_timezone, _UnsetType)
            else default_timezone
        )
        try:
            validate_timezone(merged_default_timezone)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        try:
            normalized_theme = validate_announcement_theme(
                current.announcement_theme
                if isinstance(announcement_theme, _UnsetType)
                else announcement_theme
            )
            normalized_announcement_template = validate_announcement_template(
                current.announcement_template
                if isinstance(announcement_template, _UnsetType)
                else announcement_template
            )
            ensure_safe_template(
                normalized_announcement_template,
                label="Birthday announcement template",
            )
            normalized_dm_template = validate_announcement_template(
                current.birthday_dm_template
                if isinstance(birthday_dm_template, _UnsetType)
                else birthday_dm_template
            )
            ensure_safe_template(
                normalized_dm_template,
                label="Birthday DM template",
            )
            normalized_anniversary_template = validate_announcement_template(
                current.anniversary_template
                if isinstance(anniversary_template, _UnsetType)
                else anniversary_template
            )
            ensure_safe_template(
                normalized_anniversary_template,
                label="Anniversary template",
            )
            normalized_title = validate_studio_text(
                current.announcement_title_override
                if isinstance(announcement_title_override, _UnsetType)
                else announcement_title_override,
                label="Announcement title override",
                max_length=256,
            )
            ensure_safe_text(normalized_title, label="Announcement title override")
            normalized_footer = validate_studio_text(
                current.announcement_footer_text
                if isinstance(announcement_footer_text, _UnsetType)
                else announcement_footer_text,
                label="Announcement footer text",
                max_length=512,
            )
            ensure_safe_text(normalized_footer, label="Announcement footer text")
            normalized_image_url = validate_direct_media_url(
                current.announcement_image_url
                if isinstance(announcement_image_url, _UnsetType)
                else announcement_image_url,
                label="Announcement image",
                allow_validated_marker=isinstance(announcement_image_url, _UnsetType),
            )
            normalized_thumbnail_url = validate_direct_media_url(
                current.announcement_thumbnail_url
                if isinstance(announcement_thumbnail_url, _UnsetType)
                else announcement_thumbnail_url,
                label="Announcement thumbnail",
                allow_validated_marker=isinstance(announcement_thumbnail_url, _UnsetType),
            )
            normalized_accent_color = (
                current.announcement_accent_color
                if isinstance(announcement_accent_color, _UnsetType)
                else validate_accent_color(announcement_accent_color)
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        merged = replace(
            current,
            announcement_channel_id=(
                current.announcement_channel_id
                if isinstance(announcement_channel_id, _UnsetType)
                else announcement_channel_id
            ),
            default_timezone=merged_default_timezone,
            birthday_role_id=(
                current.birthday_role_id
                if isinstance(birthday_role_id, _UnsetType)
                else birthday_role_id
            ),
            announcements_enabled=(
                current.announcements_enabled
                if isinstance(announcements_enabled, _UnsetType)
                else announcements_enabled
            ),
            role_enabled=current.role_enabled
            if isinstance(role_enabled, _UnsetType)
            else role_enabled,
            celebration_mode=(
                current.celebration_mode
                if isinstance(celebration_mode, _UnsetType)
                else celebration_mode
            ),
            announcement_theme=normalized_theme,
            announcement_template=normalized_announcement_template,
            announcement_title_override=normalized_title,
            announcement_footer_text=normalized_footer,
            announcement_image_url=normalized_image_url,
            announcement_thumbnail_url=normalized_thumbnail_url,
            announcement_accent_color=normalized_accent_color,
            birthday_dm_enabled=(
                current.birthday_dm_enabled
                if isinstance(birthday_dm_enabled, _UnsetType)
                else birthday_dm_enabled
            ),
            birthday_dm_template=normalized_dm_template,
            anniversary_enabled=(
                current.anniversary_enabled
                if isinstance(anniversary_enabled, _UnsetType)
                else anniversary_enabled
            ),
            anniversary_channel_id=(
                current.anniversary_channel_id
                if isinstance(anniversary_channel_id, _UnsetType)
                else anniversary_channel_id
            ),
            anniversary_template=normalized_anniversary_template,
            eligibility_role_id=(
                current.eligibility_role_id
                if isinstance(eligibility_role_id, _UnsetType)
                else eligibility_role_id
            ),
            ignore_bots=current.ignore_bots if isinstance(ignore_bots, _UnsetType) else ignore_bots,
            minimum_membership_days=(
                current.minimum_membership_days
                if isinstance(minimum_membership_days, _UnsetType)
                else minimum_membership_days
            ),
            mention_suppression_threshold=(
                current.mention_suppression_threshold
                if isinstance(mention_suppression_threshold, _UnsetType)
                else mention_suppression_threshold
            ),
            studio_audit_channel_id=(
                current.studio_audit_channel_id
                if isinstance(studio_audit_channel_id, _UnsetType)
                else studio_audit_channel_id
            ),
        )
        self._validate_settings(guild, merged)
        saved = await self._repository.upsert_guild_settings(merged)
        if saved.default_timezone != current.default_timezone:
            await self._repository.refresh_timezone_bound_schedules(
                guild.id,
                default_timezone=saved.default_timezone,
                now_utc=now_utc or datetime.now(UTC),
            )
        return saved

    async def update_validated_media(
        self,
        guild: discord.Guild,
        *,
        announcement_image_url: str | None | _UnsetType = UNSET,
        announcement_thumbnail_url: str | None | _UnsetType = UNSET,
    ) -> GuildSettings:
        current = await self.get_settings(guild.id)
        try:
            normalized_image_url = validate_direct_media_url(
                current.announcement_image_url
                if isinstance(announcement_image_url, _UnsetType)
                else announcement_image_url,
                label="Announcement image",
            )
            normalized_thumbnail_url = validate_direct_media_url(
                current.announcement_thumbnail_url
                if isinstance(announcement_thumbnail_url, _UnsetType)
                else announcement_thumbnail_url,
                label="Announcement thumbnail",
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        merged = replace(
            current,
            announcement_image_url=normalized_image_url,
            announcement_thumbnail_url=normalized_thumbnail_url,
        )
        self._validate_settings(guild, merged)
        return await self._repository.upsert_guild_settings(merged)

    async def describe_announcement_delivery(
        self,
        guild: discord.Guild,
    ) -> AnnouncementDeliveryReadiness:
        settings = await self.get_settings(guild.id)
        return describe_birthday_announcement_readiness(guild, settings)

    async def describe_delivery(
        self,
        guild: discord.Guild,
        *,
        kind: AnnouncementKind,
        channel_id: int | None = None,
    ) -> AnnouncementDeliveryReadiness:
        settings = await self.get_settings(guild.id)
        if kind == "birthday_dm":
            return describe_birthday_dm_readiness(settings)
        if kind == "anniversary":
            return describe_anniversary_readiness(guild, settings)
        if kind in {"recurring_event", "server_anniversary"}:
            diagnostics = (
                *build_channel_diagnostics(
                    guild,
                    channel_id=channel_id or settings.announcement_channel_id,
                    label=(
                        "server anniversary" if kind == "server_anniversary" else "recurring event"
                    ),
                ),
                *build_presentation_diagnostics(settings.presentation()),
            )
            if not diagnostics:
                return AnnouncementDeliveryReadiness(
                    status="ready",
                    summary=(
                        "Preview ready. Live server-anniversary delivery is currently ready."
                        if kind == "server_anniversary"
                        else "Preview ready. Live recurring-event delivery is currently ready."
                    ),
                )
            return AnnouncementDeliveryReadiness(
                status="blocked",
                summary=(
                    "Preview ready. Live server-anniversary delivery is blocked."
                    if kind == "server_anniversary"
                    else "Preview ready. Live recurring-event delivery is blocked."
                ),
                details=tuple(item.detail_line() for item in diagnostics),
                diagnostics=tuple(diagnostics),
            )
        return describe_birthday_announcement_readiness(guild, settings)

    async def describe_role_delivery(
        self,
        guild: discord.Guild,
    ) -> AnnouncementDeliveryReadiness:
        settings = await self.get_settings(guild.id)
        return describe_role_readiness(guild, settings)

    @staticmethod
    def _validate_settings(guild: discord.Guild, settings: GuildSettings) -> None:
        bot_member = guild.me
        if bot_member is None:
            raise ValidationError("Bot member state is unavailable. Try again in a few seconds.")

        if settings.minimum_membership_days < 0:
            raise ValidationError("Minimum membership age must be 0 days or more.")
        if (
            settings.mention_suppression_threshold < 1
            or settings.mention_suppression_threshold > 50
        ):
            raise ValidationError("Mention suppression threshold must be between 1 and 50.")

        if settings.announcement_channel_id is None and settings.announcements_enabled:
            raise ValidationError("Select an announcement channel before enabling announcements.")
        announcement_diagnostics = build_channel_diagnostics(
            guild,
            channel_id=settings.announcement_channel_id,
            label="announcement",
        )
        if settings.announcements_enabled and announcement_diagnostics:
            raise ValidationError(announcement_diagnostics[0].summary)

        if settings.role_enabled:
            if settings.birthday_role_id is None:
                raise ValidationError(
                    "Select a dedicated birthday role before enabling role assignment."
                )
            role_diagnostics = build_role_diagnostics(guild, role_id=settings.birthday_role_id)
            if role_diagnostics:
                raise ValidationError(role_diagnostics[0].summary)

        if (
            settings.eligibility_role_id is not None
            and guild.get_role(settings.eligibility_role_id) is None
        ):
            raise ValidationError("The selected eligibility role no longer exists.")

        if settings.studio_audit_channel_id is not None:
            audit_diagnostics = build_channel_diagnostics(
                guild,
                channel_id=settings.studio_audit_channel_id,
                label="studio audit",
            )
            if audit_diagnostics:
                raise ValidationError(audit_diagnostics[0].summary)

        if settings.anniversary_enabled:
            effective_anniversary_channel = (
                settings.anniversary_channel_id or settings.announcement_channel_id
            )
            if effective_anniversary_channel is None:
                raise ValidationError(
                    "Set an anniversary channel or announcement channel "
                    "before enabling anniversaries."
                )
            anniversary_diagnostics = build_channel_diagnostics(
                guild,
                channel_id=effective_anniversary_channel,
                label="anniversary",
            )
            if anniversary_diagnostics:
                raise ValidationError(anniversary_diagnostics[0].summary)
