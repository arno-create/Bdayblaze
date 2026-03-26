from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import discord

from bdayblaze.discord.announcements import (
    batch_footer,
    build_announcement_message,
    build_capsule_reveal_message,
)
from bdayblaze.discord.member_resolution import MemberResolutionError, resolve_guild_members
from bdayblaze.domain.announcement_template import (
    AnnouncementRenderRecipient,
    anniversary_years,
)
from bdayblaze.domain.models import (
    AnniversaryRecipientSnapshot,
    AnnouncementRecipientSnapshot,
    AnnouncementStudioPresentation,
    AnnouncementTheme,
    BirthdayWish,
    GuildSettings,
)
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.content_policy import ensure_safe_announcement_inputs
from bdayblaze.services.diagnostics import (
    classify_discord_http_failure,
    evaluate_member_eligibility,
)
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    DirectSendResult,
    GatewayPermanentError,
    GatewayRetryableError,
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
        expected_footer = batch_footer(cast(AnnouncementTheme, announcement_theme), batch_token)
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
                        if footer is not None and footer.endswith(expected_footer):
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
        title_override: str | None,
        footer_text: str | None,
        image_url: str | None,
        thumbnail_url: str | None,
        accent_color: int | None,
        scheduled_for_utc: datetime,
        mention_suppression_threshold: int,
        eligibility_role_id: int | None,
        ignore_bots: bool,
        minimum_membership_days: int,
    ) -> AnnouncementSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return _skip_all(recipients, "guild_missing")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return _skip_all(recipients, "announcement_channel_missing")
        if not _channel_ready(channel):
            return _skip_all(recipients, "announcement_forbidden")

        (
            render_recipients,
            delivered_user_ids,
            skipped_user_ids,
        ) = await self._resolve_birthday_recipients(
            guild,
            recipients,
            eligibility_role_id=eligibility_role_id,
            ignore_bots=ignore_bots,
            minimum_membership_days=minimum_membership_days,
        )
        if not render_recipients:
            return AnnouncementSendResult(
                message_id=None,
                delivered_user_ids=(),
                skipped_user_ids=skipped_user_ids,
            )

        try:
            ensure_safe_announcement_inputs(
                template=template,
                template_label="Birthday announcement template",
                title_override=title_override,
                footer_text=footer_text,
            )
            prepared = build_announcement_message(
                kind="birthday_announcement",
                server_name=guild.name,
                recipients=render_recipients,
                celebration_mode=celebration_mode,  # type: ignore[arg-type]
                announcement_theme=announcement_theme,  # type: ignore[arg-type]
                presentation=_presentation(
                    announcement_theme=announcement_theme,
                    title_override=title_override,
                    footer_text=footer_text,
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    accent_color=accent_color,
                ),
                template=template,
                batch_token=batch_token,
                late_delivery=_late_delivery(scheduled_for_utc),
                mention_suppressed=len(render_recipients) >= mention_suppression_threshold,
            )
        except ValueError as exc:
            raise GatewayPermanentError(_invalid_delivery_code(str(exc))) from exc
        try:
            message = await channel.send(
                content=prepared.content,
                embed=prepared.embed,
                allowed_mentions=(
                    discord.AllowedMentions.none()
                    if prepared.content == ""
                    else discord.AllowedMentions(users=True)
                ),
            )
        except discord.Forbidden:
            return _skip_all(recipients, "announcement_forbidden")
        except discord.HTTPException as exc:
            failure = classify_discord_http_failure(exc, surface="announcement")
            if failure.permanent:
                raise GatewayPermanentError(failure.code) from exc
            raise GatewayRetryableError(failure.code) from exc
        self._logger.info(
            "announcement_sent",
            guild_id=guild_id,
            member_count=len(delivered_user_ids),
        )
        return AnnouncementSendResult(
            message_id=message.id,
            delivered_user_ids=tuple(delivered_user_ids),
            skipped_user_ids=skipped_user_ids,
            note_code="late_delivery" if _late_delivery(scheduled_for_utc) else None,
        )

    async def send_anniversary_announcement(
        self,
        *,
        guild_id: int,
        channel_id: int,
        recipients: list[AnniversaryRecipientSnapshot],
        celebration_mode: str,
        announcement_theme: str,
        batch_token: str,
        template: str,
        title_override: str | None,
        footer_text: str | None,
        image_url: str | None,
        thumbnail_url: str | None,
        accent_color: int | None,
        scheduled_for_utc: datetime,
        event_name: str,
        event_month: int,
        event_day: int,
        mention_suppression_threshold: int,
        eligibility_role_id: int | None,
        ignore_bots: bool,
        minimum_membership_days: int,
    ) -> AnnouncementSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return _skip_all(recipients, "guild_missing")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return _skip_all(recipients, "announcement_channel_missing")
        if not _channel_ready(channel):
            return _skip_all(recipients, "announcement_forbidden")

        (
            render_recipients,
            delivered_user_ids,
            skipped_user_ids,
        ) = await self._resolve_anniversary_recipients(
            guild,
            recipients,
            eligibility_role_id=eligibility_role_id,
            ignore_bots=ignore_bots,
            minimum_membership_days=minimum_membership_days,
        )
        if not render_recipients:
            return AnnouncementSendResult(
                message_id=None,
                delivered_user_ids=(),
                skipped_user_ids=skipped_user_ids,
            )

        try:
            ensure_safe_announcement_inputs(
                template=template,
                template_label="Anniversary template",
                title_override=title_override,
                footer_text=footer_text,
                event_name=event_name,
            )
            prepared = build_announcement_message(
                kind="anniversary",
                server_name=guild.name,
                recipients=render_recipients,
                celebration_mode=celebration_mode,  # type: ignore[arg-type]
                announcement_theme=announcement_theme,  # type: ignore[arg-type]
                presentation=_presentation(
                    announcement_theme=announcement_theme,
                    title_override=title_override,
                    footer_text=footer_text,
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    accent_color=accent_color,
                ),
                template=template,
                batch_token=batch_token,
                event_name=event_name,
                event_month=event_month,
                event_day=event_day,
                late_delivery=_late_delivery(scheduled_for_utc),
                mention_suppressed=len(render_recipients) >= mention_suppression_threshold,
            )
        except ValueError as exc:
            raise GatewayPermanentError(_invalid_delivery_code(str(exc))) from exc
        try:
            message = await channel.send(
                content=prepared.content,
                embed=prepared.embed,
                allowed_mentions=(
                    discord.AllowedMentions.none()
                    if prepared.content == ""
                    else discord.AllowedMentions(users=True)
                ),
            )
        except discord.Forbidden:
            return _skip_all(recipients, "announcement_forbidden")
        except discord.HTTPException as exc:
            failure = classify_discord_http_failure(exc, surface="announcement")
            if failure.permanent:
                raise GatewayPermanentError(failure.code) from exc
            raise GatewayRetryableError(failure.code) from exc
        self._logger.info(
            "anniversary_announcement_sent",
            guild_id=guild_id,
            member_count=len(delivered_user_ids),
        )
        return AnnouncementSendResult(
            message_id=message.id,
            delivered_user_ids=tuple(delivered_user_ids),
            skipped_user_ids=skipped_user_ids,
            note_code="late_delivery" if _late_delivery(scheduled_for_utc) else None,
        )

    async def send_birthday_dm(
        self,
        *,
        guild_id: int,
        user_id: int,
        celebration_mode: str,
        announcement_theme: str,
        template: str,
        birth_month: int,
        birth_day: int,
        timezone: str,
        eligibility_role_id: int | None,
        ignore_bots: bool,
        minimum_membership_days: int,
        scheduled_for_utc: datetime,
    ) -> DirectSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return DirectSendResult(status="guild_missing")
        member = await self._fetch_member(guild, user_id)
        if member is None:
            return DirectSendResult(status="member_missing")
        decision = evaluate_member_eligibility(
            settings=_eligibility_settings(
                guild_id,
                eligibility_role_id=eligibility_role_id,
                ignore_bots=ignore_bots,
                minimum_membership_days=minimum_membership_days,
            ),
            member=member,
        )
        if not decision.allowed:
            return DirectSendResult(status=decision.code or "member_ineligible")
        try:
            ensure_safe_announcement_inputs(
                template=template,
                template_label="Birthday DM template",
                title_override=None,
                footer_text=None,
            )
            prepared = build_announcement_message(
                kind="birthday_dm",
                server_name=guild.name,
                recipients=[
                    AnnouncementRenderRecipient(
                        mention=member.mention,
                        display_name=member.display_name,
                        username=member.name,
                        birth_month=birth_month,
                        birth_day=birth_day,
                        timezone=timezone,
                    )
                ],
                celebration_mode=celebration_mode,  # type: ignore[arg-type]
                announcement_theme=announcement_theme,  # type: ignore[arg-type]
                presentation=_presentation(
                    announcement_theme=announcement_theme,
                    title_override=None,
                    footer_text=None,
                    image_url=None,
                    thumbnail_url=None,
                    accent_color=None,
                ),
                template=template,
                late_delivery=_late_delivery(scheduled_for_utc),
            )
        except ValueError as exc:
            raise GatewayPermanentError(_invalid_delivery_code(str(exc))) from exc
        try:
            await member.send(embed=prepared.embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return DirectSendResult(status="dm_forbidden")
        except discord.HTTPException as exc:
            failure = classify_discord_http_failure(exc, surface="birthday_dm")
            if failure.permanent:
                raise GatewayPermanentError(failure.code) from exc
            raise GatewayRetryableError(failure.code) from exc
        return DirectSendResult(
            status="sent",
            note_code="late_delivery" if _late_delivery(scheduled_for_utc) else None,
        )

    async def send_recurring_announcement(
        self,
        *,
        guild_id: int,
        channel_id: int,
        celebration_kind: str,
        celebration_mode: str,
        announcement_theme: str,
        template: str | None,
        title_override: str | None,
        footer_text: str | None,
        image_url: str | None,
        thumbnail_url: str | None,
        accent_color: int | None,
        event_name: str,
        event_month: int,
        event_day: int,
        scheduled_for_utc: datetime,
    ) -> DirectSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return DirectSendResult(status="guild_missing")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return DirectSendResult(status="announcement_channel_missing")
        if not _channel_ready(channel):
            return DirectSendResult(status="announcement_forbidden")
        try:
            ensure_safe_announcement_inputs(
                template=template,
                template_label=(
                    "Server anniversary template"
                    if celebration_kind == "server_anniversary"
                    else "Recurring event template"
                ),
                title_override=title_override,
                footer_text=footer_text,
                event_name=event_name,
            )
            prepared = build_announcement_message(
                kind=(
                    "server_anniversary"
                    if celebration_kind == "server_anniversary"
                    else "recurring_event"
                ),
                server_name=guild.name,
                recipients=[],
                celebration_mode=celebration_mode,  # type: ignore[arg-type]
                announcement_theme=announcement_theme,  # type: ignore[arg-type]
                presentation=_presentation(
                    announcement_theme=announcement_theme,
                    title_override=title_override,
                    footer_text=footer_text,
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    accent_color=accent_color,
                ),
                template=template,
                event_name=event_name,
                event_month=event_month,
                event_day=event_day,
                late_delivery=_late_delivery(scheduled_for_utc),
            )
        except ValueError as exc:
            raise GatewayPermanentError(_invalid_delivery_code(str(exc))) from exc
        try:
            message = await channel.send(
                embed=prepared.embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            return DirectSendResult(status="announcement_forbidden")
        except discord.HTTPException as exc:
            failure = classify_discord_http_failure(exc, surface="announcement")
            if failure.permanent:
                raise GatewayPermanentError(failure.code) from exc
            raise GatewayRetryableError(failure.code) from exc
        return DirectSendResult(
            status="sent",
            message_id=message.id,
            note_code="late_delivery" if _late_delivery(scheduled_for_utc) else None,
        )

    async def send_capsule_reveal(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        celebration_mode: str,
        announcement_theme: str,
        birth_month: int,
        birth_day: int,
        timezone: str,
        wishes: list[BirthdayWish],
        scheduled_for_utc: datetime,
    ) -> DirectSendResult:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return DirectSendResult(status="guild_missing")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return DirectSendResult(status="announcement_channel_missing")
        if not _channel_ready(channel):
            return DirectSendResult(status="announcement_forbidden")
        resolved = await self._resolve_members(
            guild,
            [user_id, *[wish.author_user_id for wish in wishes]],
        )
        resolved_by_user_id = {resolved_user_id: member for resolved_user_id, member in resolved}
        birthday_member = resolved_by_user_id.get(user_id)
        if birthday_member is None:
            return DirectSendResult(status="member_missing")
        prepared = build_capsule_reveal_message(
            birthday_member=AnnouncementRenderRecipient(
                mention=birthday_member.mention,
                display_name=birthday_member.display_name,
                username=birthday_member.name,
                birth_month=birth_month,
                birth_day=birth_day,
                timezone=timezone,
            ),
            wishes=[
                (
                    (
                        AnnouncementRenderRecipient(
                            mention=author.mention,
                            display_name=author.display_name,
                            username=author.name,
                            birth_month=birth_month,
                            birth_day=birth_day,
                            timezone=timezone,
                        )
                        if author is not None
                        else None
                    ),
                    wish,
                )
                for wish in wishes
                for author in [resolved_by_user_id.get(wish.author_user_id)]
            ],
            celebration_mode=celebration_mode,  # type: ignore[arg-type]
            announcement_theme=announcement_theme,  # type: ignore[arg-type]
            late_delivery=_late_delivery(scheduled_for_utc),
        )
        try:
            message = await channel.send(
                content=prepared.content,
                embeds=list(prepared.embeds),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.Forbidden:
            return DirectSendResult(status="announcement_forbidden")
        except discord.HTTPException as exc:
            failure = classify_discord_http_failure(exc, surface="announcement")
            if failure.permanent:
                raise GatewayPermanentError(failure.code) from exc
            raise GatewayRetryableError(failure.code) from exc
        return DirectSendResult(
            status="sent",
            message_id=message.id,
            note_code="late_delivery" if _late_delivery(scheduled_for_utc) else None,
        )

    async def add_birthday_role(
        self,
        *,
        guild_id: int,
        user_id: int,
        role_id: int,
        eligibility_role_id: int | None,
        ignore_bots: bool,
        minimum_membership_days: int,
    ) -> str:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return "guild_missing"
        role = guild.get_role(role_id)
        if role is None:
            return "role_missing"
        member = await self._fetch_member(guild, user_id)
        if member is None:
            return "member_missing"
        decision = evaluate_member_eligibility(
            settings=_eligibility_settings(
                guild_id,
                eligibility_role_id=eligibility_role_id,
                ignore_bots=ignore_bots,
                minimum_membership_days=minimum_membership_days,
            ),
            member=member,
        )
        if not decision.allowed:
            return decision.code or "member_ineligible"
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

    async def _resolve_birthday_recipients(
        self,
        guild: discord.Guild,
        recipients: list[AnnouncementRecipientSnapshot],
        *,
        eligibility_role_id: int | None,
        ignore_bots: bool,
        minimum_membership_days: int,
    ) -> tuple[list[AnnouncementRenderRecipient], list[int], dict[int, str]]:
        resolved_members = await self._resolve_members(
            guild, [recipient.user_id for recipient in recipients]
        )
        by_user_id = {user_id: member for user_id, member in resolved_members}
        render_recipients: list[AnnouncementRenderRecipient] = []
        delivered_user_ids: list[int] = []
        skipped_user_ids: dict[int, str] = {}
        for recipient in recipients:
            member = by_user_id.get(recipient.user_id)
            if member is None:
                skipped_user_ids[recipient.user_id] = "member_missing"
                continue
            decision = evaluate_member_eligibility(
                settings=_eligibility_settings(
                    guild.id,
                    eligibility_role_id=eligibility_role_id,
                    ignore_bots=ignore_bots,
                    minimum_membership_days=minimum_membership_days,
                ),
                member=member,
            )
            if not decision.allowed:
                skipped_user_ids[recipient.user_id] = decision.code or "member_ineligible"
                continue
            delivered_user_ids.append(recipient.user_id)
            render_recipients.append(
                AnnouncementRenderRecipient(
                    mention=member.mention,
                    display_name=member.display_name,
                    username=member.name,
                    birth_month=recipient.birth_month,
                    birth_day=recipient.birth_day,
                    timezone=recipient.timezone,
                )
            )
        return render_recipients, delivered_user_ids, skipped_user_ids

    async def _resolve_anniversary_recipients(
        self,
        guild: discord.Guild,
        recipients: list[AnniversaryRecipientSnapshot],
        *,
        eligibility_role_id: int | None,
        ignore_bots: bool,
        minimum_membership_days: int,
    ) -> tuple[list[AnnouncementRenderRecipient], list[int], dict[int, str]]:
        resolved_members = await self._resolve_members(
            guild, [recipient.user_id for recipient in recipients]
        )
        by_user_id = {user_id: member for user_id, member in resolved_members}
        render_recipients: list[AnnouncementRenderRecipient] = []
        delivered_user_ids: list[int] = []
        skipped_user_ids: dict[int, str] = {}
        now_utc = datetime.now(UTC)
        for recipient in recipients:
            member = by_user_id.get(recipient.user_id)
            if member is None:
                skipped_user_ids[recipient.user_id] = "member_missing"
                continue
            decision = evaluate_member_eligibility(
                settings=_eligibility_settings(
                    guild.id,
                    eligibility_role_id=eligibility_role_id,
                    ignore_bots=ignore_bots,
                    minimum_membership_days=minimum_membership_days,
                ),
                member=member,
            )
            if not decision.allowed:
                skipped_user_ids[recipient.user_id] = decision.code or "member_ineligible"
                continue
            delivered_user_ids.append(recipient.user_id)
            render_recipients.append(
                AnnouncementRenderRecipient(
                    mention=member.mention,
                    display_name=member.display_name,
                    username=member.name,
                    anniversary_years=anniversary_years(recipient.joined_at_utc, now_utc=now_utc),
                )
            )
        return render_recipients, delivered_user_ids, skipped_user_ids

    async def _resolve_members(
        self,
        guild: discord.Guild,
        user_ids: list[int],
    ) -> list[tuple[int, discord.Member]]:
        try:
            return await resolve_guild_members(
                guild,
                user_ids,
                raise_on_http_error=True,
            )
        except MemberResolutionError as exc:
            raise GatewayRetryableError("member_lookup_http_error") from exc

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        resolved = await self._resolve_members(guild, [user_id])
        if not resolved:
            return None
        return resolved[0][1]


def _channel_ready(channel: discord.TextChannel) -> bool:
    guild = channel.guild
    bot_member = guild.me
    if bot_member is None:
        return False
    permissions = channel.permissions_for(bot_member)
    return permissions.view_channel and permissions.send_messages and permissions.embed_links


def _presentation(
    *,
    announcement_theme: str,
    title_override: str | None,
    footer_text: str | None,
    image_url: str | None,
    thumbnail_url: str | None,
    accent_color: int | None,
) -> AnnouncementStudioPresentation:
    return AnnouncementStudioPresentation(
        theme=announcement_theme,  # type: ignore[arg-type]
        title_override=title_override,
        footer_text=footer_text,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        accent_color=accent_color,
    )


def _eligibility_settings(
    guild_id: int,
    *,
    eligibility_role_id: int | None,
    ignore_bots: bool,
    minimum_membership_days: int,
) -> GuildSettings:
    defaults = GuildSettings.default(guild_id)
    return GuildSettings(
        guild_id=defaults.guild_id,
        default_timezone=defaults.default_timezone,
        birthday_role_id=defaults.birthday_role_id,
        announcements_enabled=defaults.announcements_enabled,
        role_enabled=defaults.role_enabled,
        celebration_mode=defaults.celebration_mode,
        announcement_theme=defaults.announcement_theme,
        announcement_template=defaults.announcement_template,
        announcement_title_override=defaults.announcement_title_override,
        announcement_footer_text=defaults.announcement_footer_text,
        announcement_accent_color=defaults.announcement_accent_color,
        birthday_dm_enabled=defaults.birthday_dm_enabled,
        birthday_dm_template=defaults.birthday_dm_template,
        anniversary_enabled=defaults.anniversary_enabled,
        anniversary_template=defaults.anniversary_template,
        eligibility_role_id=eligibility_role_id,
        ignore_bots=ignore_bots,
        minimum_membership_days=minimum_membership_days,
        mention_suppression_threshold=defaults.mention_suppression_threshold,
        studio_audit_channel_id=defaults.studio_audit_channel_id,
    )


def _skip_all(
    recipients: list[AnnouncementRecipientSnapshot] | list[AnniversaryRecipientSnapshot],
    code: str,
) -> AnnouncementSendResult:
    return AnnouncementSendResult(
        message_id=None,
        delivered_user_ids=(),
        skipped_user_ids={recipient.user_id: code for recipient in recipients},
    )


def _late_delivery(scheduled_for_utc: datetime) -> bool:
    return datetime.now(UTC) - scheduled_for_utc > timedelta(minutes=1)


def _invalid_delivery_code(message: str) -> str:
    lowered = message.lower()
    if "image" in lowered or "thumbnail" in lowered or "url" in lowered:
        return "invalid_media_url"
    return "invalid_announcement_payload"
