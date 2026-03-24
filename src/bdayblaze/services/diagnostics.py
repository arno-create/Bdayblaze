from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import discord

from bdayblaze.domain.birthday_logic import membership_age_days
from bdayblaze.domain.models import (
    AnnouncementDeliveryReadiness,
    AnnouncementDeliveryStatus,
    DeliveryDiagnostic,
    GuildSettings,
)


@dataclass(slots=True, frozen=True)
class EligibilityDecision:
    allowed: bool
    code: str | None = None
    summary: str | None = None


def build_channel_diagnostics(
    guild: discord.Guild,
    *,
    channel_id: int | None,
    label: str,
) -> tuple[DeliveryDiagnostic, ...]:
    bot_member = guild.me
    if bot_member is None:
        return (
            DeliveryDiagnostic(
                severity="warning",
                code="bot_member_unavailable",
                summary="Bot member state is still loading.",
                action="Wait a few seconds, then try again.",
            ),
        )
    if channel_id is None:
        return (
            DeliveryDiagnostic(
                severity="error",
                code=f"{label}_missing",
                summary=f"No {label.replace('_', ' ')} channel is configured.",
                action="Pick a valid text channel first.",
            ),
        )
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return (
            DeliveryDiagnostic(
                severity="error",
                code=f"{label}_deleted",
                summary=f"The saved {label.replace('_', ' ')} channel is missing or invalid.",
                action="Pick a new text or announcement channel.",
            ),
        )
    permissions = channel.permissions_for(bot_member)
    diagnostics: list[DeliveryDiagnostic] = []
    if not permissions.view_channel:
        diagnostics.append(
            DeliveryDiagnostic(
                severity="error",
                code="missing_view_channel",
                summary=f"The bot cannot view #{channel.name}.",
                action="Grant View Channel in that channel.",
            )
        )
    if not permissions.send_messages:
        diagnostics.append(
            DeliveryDiagnostic(
                severity="error",
                code="missing_send_messages",
                summary=f"The bot cannot send messages in #{channel.name}.",
                action="Grant Send Messages in that channel.",
            )
        )
    if not permissions.embed_links:
        diagnostics.append(
            DeliveryDiagnostic(
                severity="error",
                code="missing_embed_links",
                summary=f"The bot cannot send embeds in #{channel.name}.",
                action="Grant Embed Links in that channel.",
            )
        )
    return tuple(diagnostics)


def build_role_diagnostics(
    guild: discord.Guild,
    *,
    role_id: int | None,
) -> tuple[DeliveryDiagnostic, ...]:
    if role_id is None:
        return ()
    bot_member = guild.me
    if bot_member is None:
        return (
            DeliveryDiagnostic(
                severity="warning",
                code="bot_member_unavailable",
                summary="Bot member state is still loading.",
                action="Wait a few seconds, then try again.",
            ),
        )
    role = guild.get_role(role_id)
    if role is None:
        return (
            DeliveryDiagnostic(
                severity="error",
                code="birthday_role_missing",
                summary="The configured birthday role no longer exists.",
                action="Pick a new dedicated birthday role or disable role assignment.",
            ),
        )
    diagnostics: list[DeliveryDiagnostic] = []
    if role.is_default() or role.managed:
        diagnostics.append(
            DeliveryDiagnostic(
                severity="error",
                code="birthday_role_invalid",
                summary="The configured birthday role is not a dedicated bot-manageable role.",
                action="Choose a normal server role that is not managed and not @everyone.",
            )
        )
    if not bot_member.guild_permissions.manage_roles:
        diagnostics.append(
            DeliveryDiagnostic(
                severity="error",
                code="manage_roles_missing",
                summary="The bot is missing Manage Roles.",
                action="Grant Manage Roles or disable birthday role assignment.",
            )
        )
    elif bot_member.top_role <= role:
        diagnostics.append(
            DeliveryDiagnostic(
                severity="error",
                code="role_hierarchy_invalid",
                summary="The birthday role is above the bot in the role hierarchy.",
                action="Move the bot's highest role above the dedicated birthday role.",
            )
        )
    return tuple(diagnostics)


def describe_birthday_announcement_readiness(
    guild: discord.Guild,
    settings: GuildSettings,
) -> AnnouncementDeliveryReadiness:
    if not settings.announcements_enabled:
        disabled_diagnostics: tuple[DeliveryDiagnostic, ...] = (
            DeliveryDiagnostic(
                severity="warning",
                code="announcements_disabled",
                summary="Birthday announcements are disabled in this server.",
                action="Enable announcements in setup when you are ready to post live.",
            ),
        )
        return AnnouncementDeliveryReadiness(
            status="blocked",
            summary="Preview ready. Live birthday announcements are disabled.",
            details=tuple(item.detail_line() for item in disabled_diagnostics),
            diagnostics=disabled_diagnostics,
        )
    diagnostics = build_channel_diagnostics(
        guild,
        channel_id=settings.announcement_channel_id,
        label="announcement",
    )
    return _readiness_from_diagnostics(
        diagnostics,
        ready_summary="Preview ready. Live birthday announcements are currently ready.",
        blocked_summary="Preview ready. Live birthday announcements are blocked.",
    )


def describe_anniversary_readiness(
    guild: discord.Guild,
    settings: GuildSettings,
) -> AnnouncementDeliveryReadiness:
    if not settings.anniversary_enabled:
        disabled_diagnostics: tuple[DeliveryDiagnostic, ...] = (
            DeliveryDiagnostic(
                severity="warning",
                code="anniversary_disabled",
                summary="Join-anniversary announcements are disabled in this server.",
                action="Enable anniversary announcements in setup when you want them live.",
            ),
        )
        return AnnouncementDeliveryReadiness(
            status="blocked",
            summary="Preview ready. Live anniversary announcements are disabled.",
            details=tuple(item.detail_line() for item in disabled_diagnostics),
            diagnostics=disabled_diagnostics,
        )
    effective_channel_id = settings.anniversary_channel_id or settings.announcement_channel_id
    diagnostics = build_channel_diagnostics(
        guild,
        channel_id=effective_channel_id,
        label="anniversary",
    )
    return _readiness_from_diagnostics(
        diagnostics,
        ready_summary="Preview ready. Live anniversary announcements are currently ready.",
        blocked_summary="Preview ready. Live anniversary announcements are blocked.",
    )


def describe_role_readiness(
    guild: discord.Guild,
    settings: GuildSettings,
) -> AnnouncementDeliveryReadiness:
    if not settings.role_enabled:
        disabled_diagnostics: tuple[DeliveryDiagnostic, ...] = (
            DeliveryDiagnostic(
                severity="warning",
                code="role_assignment_disabled",
                summary="Birthday role assignment is disabled in this server.",
                action="Enable it only if you want the bot to manage a dedicated birthday role.",
            ),
        )
        return AnnouncementDeliveryReadiness(
            status="blocked",
            summary="Preview ready. Live birthday role assignment is disabled.",
            details=tuple(item.detail_line() for item in disabled_diagnostics),
            diagnostics=disabled_diagnostics,
        )
    diagnostics = build_role_diagnostics(guild, role_id=settings.birthday_role_id)
    return _readiness_from_diagnostics(
        diagnostics,
        ready_summary="Preview ready. Live birthday role assignment is currently ready.",
        blocked_summary="Preview ready. Live birthday role assignment is blocked.",
    )


def describe_birthday_dm_readiness(settings: GuildSettings) -> AnnouncementDeliveryReadiness:
    if not settings.birthday_dm_enabled:
        disabled_diagnostics: tuple[DeliveryDiagnostic, ...] = (
            DeliveryDiagnostic(
                severity="warning",
                code="birthday_dm_disabled",
                summary="Birthday DMs are disabled in this server.",
                action="Enable birthday DMs if you want members to get a private greeting.",
            ),
        )
        return AnnouncementDeliveryReadiness(
            status="blocked",
            summary="Preview ready. Live birthday DMs are disabled.",
            details=tuple(item.detail_line() for item in disabled_diagnostics),
            diagnostics=disabled_diagnostics,
        )
    diagnostics = (
        DeliveryDiagnostic(
            severity="info",
            code="dm_best_effort",
            summary="Birthday DMs are best-effort and depend on the member's DM settings.",
            action=(
                "Use preview to verify formatting. Closed DMs will fail quietly at delivery time."
            ),
        ),
    )
    return AnnouncementDeliveryReadiness(
        status="ready",
        summary="Preview ready. Live birthday DMs are best-effort per member.",
        details=tuple(item.detail_line() for item in diagnostics),
        diagnostics=diagnostics,
    )


def evaluate_member_eligibility(
    *,
    settings: GuildSettings,
    member: discord.Member,
    now_utc: datetime | None = None,
) -> EligibilityDecision:
    current = now_utc or datetime.now(UTC)
    if settings.ignore_bots and member.bot:
        return EligibilityDecision(
            allowed=False,
            code="bot_ignored",
            summary="This member is excluded because bot accounts are ignored.",
        )
    if settings.eligibility_role_id is not None:
        role = discord.utils.get(member.roles, id=settings.eligibility_role_id)
        if role is None:
            return EligibilityDecision(
                allowed=False,
                code="eligibility_role_missing",
                summary="This member is excluded because they do not have the eligibility role.",
            )
    if settings.minimum_membership_days > 0:
        age_days = membership_age_days(member.joined_at, now_utc=current)
        if age_days is None or age_days < settings.minimum_membership_days:
            return EligibilityDecision(
                allowed=False,
                code="membership_age_unmet",
                summary=(
                    "This member is excluded because they have not been in the server long "
                    "enough yet."
                ),
            )
    return EligibilityDecision(allowed=True)


def _readiness_from_diagnostics(
    diagnostics: tuple[DeliveryDiagnostic, ...],
    *,
    ready_summary: str,
    blocked_summary: str,
) -> AnnouncementDeliveryReadiness:
    status: AnnouncementDeliveryStatus = "ready" if not diagnostics else "blocked"
    summary = ready_summary if status == "ready" else blocked_summary
    return AnnouncementDeliveryReadiness(
        status=status,
        summary=summary,
        details=tuple(item.detail_line() for item in diagnostics),
        diagnostics=diagnostics,
    )
