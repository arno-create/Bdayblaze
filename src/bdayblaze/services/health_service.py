from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord

from bdayblaze.domain.birthday_logic import validate_timezone
from bdayblaze.domain.models import HealthIssue, SchedulerMetrics
from bdayblaze.repositories.postgres import PostgresRepository


class HealthService:
    def __init__(
        self,
        repository: PostgresRepository,
        metrics: SchedulerMetrics,
        *,
        recovery_grace_hours: int,
        scheduler_max_sleep_seconds: int,
    ) -> None:
        self._repository = repository
        self._metrics = metrics
        self._recovery_grace_hours = recovery_grace_hours
        self._scheduler_max_sleep_seconds = scheduler_max_sleep_seconds

    async def inspect_guild(self, guild: discord.Guild) -> list[HealthIssue]:
        issues: list[HealthIssue] = []
        settings = await self._repository.fetch_guild_settings(guild.id)
        if settings is None:
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="missing_config",
                    summary="Server setup is still using defaults.",
                    action="Run /birthday setup to configure timezone, channel, and optional role.",
                )
            )
        else:
            try:
                validate_timezone(settings.default_timezone)
            except ValueError:
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="invalid_timezone",
                        summary="The saved default timezone is invalid.",
                        action="Open /birthday setup and save a valid IANA timezone.",
                    )
                )

            bot_member = guild.me
            if bot_member is None:
                issues.append(
                    HealthIssue(
                        severity="warning",
                        code="bot_member_unavailable",
                        summary="The bot member state is not ready yet.",
                        action="Wait a few seconds and re-run /birthday health.",
                    )
                )
            else:
                if settings.announcements_enabled:
                    if settings.announcement_channel_id is None:
                        issues.append(
                            HealthIssue(
                                severity="error",
                                code="announcement_channel_missing",
                                summary="Announcements are enabled without a configured channel.",
                                action="Select a valid announcement channel in /birthday setup.",
                            )
                        )
                    else:
                        channel = guild.get_channel(settings.announcement_channel_id)
                        if not isinstance(channel, discord.TextChannel):
                            issues.append(
                                HealthIssue(
                                    severity="error",
                                    code="announcement_channel_deleted",
                                    summary=(
                                        "The configured announcement channel is missing or invalid."
                                    ),
                                    action=(
                                        "Pick a new text or announcement channel in /birthday "
                                        "setup."
                                    ),
                                )
                            )
                        else:
                            permissions = channel.permissions_for(bot_member)
                            if (
                                not permissions.view_channel
                                or not permissions.send_messages
                                or not permissions.embed_links
                            ):
                                issues.append(
                                    HealthIssue(
                                        severity="error",
                                        code="announcement_permissions",
                                        summary=(
                                            "The bot cannot announce birthdays in the configured "
                                            "channel."
                                        ),
                                        action=(
                                            "Grant View Channel, Send Messages, and Embed Links "
                                            "there."
                                        ),
                                    )
                                )

                if settings.role_enabled:
                    if settings.birthday_role_id is None:
                        issues.append(
                            HealthIssue(
                                severity="error",
                                code="birthday_role_missing",
                                summary="Role assignment is enabled without a configured role.",
                                action="Select a dedicated birthday role in /birthday setup.",
                            )
                        )
                    else:
                        role = guild.get_role(settings.birthday_role_id)
                        if role is None:
                            issues.append(
                                HealthIssue(
                                    severity="error",
                                    code="birthday_role_deleted",
                                    summary="The configured birthday role no longer exists.",
                                    action="Select a replacement role or disable role assignment.",
                                )
                            )
                        else:
                            if not bot_member.guild_permissions.manage_roles:
                                issues.append(
                                    HealthIssue(
                                        severity="error",
                                        code="manage_roles_missing",
                                        summary="The bot is missing Manage Roles.",
                                        action=(
                                            "Grant Manage Roles or disable birthday role "
                                            "assignment."
                                        ),
                                    )
                                )
                            elif bot_member.top_role <= role:
                                issues.append(
                                    HealthIssue(
                                        severity="error",
                                        code="role_hierarchy_invalid",
                                        summary=(
                                            "The birthday role is above the bot in the role "
                                            "hierarchy."
                                        ),
                                        action=(
                                            "Move the bot's top role above the dedicated birthday "
                                            "role."
                                        ),
                                    )
                                )

        now_utc = datetime.now(UTC)
        stale_window = timedelta(seconds=max(self._scheduler_max_sleep_seconds * 2, 600))
        backlog = await self._repository.fetch_scheduler_backlog(now_utc, stale_window)

        oldest_due = min(
            [
                ts
                for ts in [
                    backlog.oldest_due_birthday_utc,
                    backlog.oldest_due_role_removal_utc,
                    backlog.oldest_due_event_utc,
                ]
                if ts is not None
            ],
            default=None,
        )
        if oldest_due is not None:
            lag = now_utc - oldest_due
            if lag > timedelta(hours=self._recovery_grace_hours):
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="scheduler_recovery_window_exceeded",
                        summary="Scheduler backlog is older than the configured recovery window.",
                        action=(
                            "Inspect the worker, then manually reconcile missed celebrations "
                            "before resuming."
                        ),
                    )
                )
            elif lag > timedelta(minutes=10):
                issues.append(
                    HealthIssue(
                        severity="warning",
                        code="scheduler_lag",
                        summary="Scheduler work is running behind.",
                        action="Keep the bot online and re-check after the backlog clears.",
                    )
                )

        if backlog.stale_processing_count > 0:
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="stale_processing_events",
                    summary=(
                        "Some celebration events were left mid-processing and had to be recovered."
                    ),
                    action="Review logs for Discord API errors and confirm celebrations completed.",
                )
            )

        if self._metrics.last_iteration_at_utc is None:
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="scheduler_not_started",
                    summary="Scheduler has not recorded a run yet.",
                    action="Wait for startup to finish, then re-run /birthday health.",
                )
            )
        elif now_utc - self._metrics.last_iteration_at_utc > stale_window:
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="scheduler_stalled",
                    summary="Scheduler heartbeat is stale.",
                    action="Restart the bot process and verify database connectivity.",
                )
            )

        if not self._metrics.recovery_completed:
            issues.append(
                HealthIssue(
                    severity="info",
                    code="recovery_incomplete",
                    summary="Startup recovery has not completed yet.",
                    action="Re-run /birthday health after the bot has been online for a minute.",
                )
            )

        return issues
