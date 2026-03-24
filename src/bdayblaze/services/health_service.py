from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord

from bdayblaze.domain.birthday_logic import validate_timezone
from bdayblaze.domain.models import HealthIssue, SchedulerMetrics
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.diagnostics import build_channel_diagnostics, build_role_diagnostics


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
                    action=(
                        "Run /birthday setup to configure timezone, channels, and delivery rules."
                    ),
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

            for diagnostic in build_channel_diagnostics(
                guild,
                channel_id=(
                    settings.announcement_channel_id if settings.announcements_enabled else None
                ),
                label="announcement",
            ):
                if settings.announcements_enabled:
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=diagnostic.code,
                            summary=diagnostic.summary,
                            action=(
                                diagnostic.action or "Review the configured announcement channel."
                            ),
                        )
                    )

            anniversary_channel_id = (
                settings.anniversary_channel_id or settings.announcement_channel_id
            )
            for diagnostic in build_channel_diagnostics(
                guild,
                channel_id=anniversary_channel_id if settings.anniversary_enabled else None,
                label="anniversary",
            ):
                if settings.anniversary_enabled:
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=diagnostic.code,
                            summary=diagnostic.summary,
                            action=(
                                diagnostic.action or "Review the configured anniversary channel."
                            ),
                        )
                    )

            for diagnostic in build_role_diagnostics(
                guild,
                role_id=settings.birthday_role_id if settings.role_enabled else None,
            ):
                if settings.role_enabled:
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=diagnostic.code,
                            summary=diagnostic.summary,
                            action=diagnostic.action or "Review the configured birthday role.",
                        )
                    )

            if (
                settings.eligibility_role_id is not None
                and guild.get_role(settings.eligibility_role_id) is None
            ):
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="eligibility_role_missing",
                        summary="The configured eligibility role no longer exists.",
                        action="Pick a new eligibility role or clear the requirement.",
                    )
                )

            recurring_events = await self._repository.list_recurring_celebrations(
                guild.id,
                limit=20,
            )
            for celebration in recurring_events:
                if not celebration.enabled:
                    continue
                effective_channel_id = celebration.channel_id or settings.announcement_channel_id
                for diagnostic in build_channel_diagnostics(
                    guild,
                    channel_id=effective_channel_id,
                    label="recurring event",
                ):
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=f"recurring_event_{celebration.id}_{diagnostic.code}",
                            summary=(
                                f"Recurring event '{celebration.name}' is blocked: "
                                f"{diagnostic.summary}"
                            ),
                            action=(
                                diagnostic.action or "Review the recurring event channel override."
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
                    backlog.oldest_due_anniversary_utc,
                    backlog.oldest_due_recurring_utc,
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

        recent_issues = await self._repository.list_recent_delivery_issues(
            guild.id,
            since_utc=now_utc - timedelta(days=7),
            limit=5,
        )
        for recent_issue in recent_issues:
            if recent_issue.last_error_code == "late_delivery":
                issues.append(
                    HealthIssue(
                        severity="info",
                        code="recent_late_delivery",
                        summary=(
                            f"Recent {recent_issue.event_kind} delivery was recovered late "
                            "but completed."
                        ),
                        action=(
                            "Review recent uptime or Discord API failures if late recoveries "
                            "keep appearing."
                        ),
                    )
                )
                continue
            issues.append(
                HealthIssue(
                    severity="info",
                    code=f"recent_{recent_issue.last_error_code}",
                    summary=(
                        f"Recent {recent_issue.event_kind} issue: "
                        f"{recent_issue.last_error_code or 'unknown'}."
                    ),
                    action="Review recent logs and preview the affected delivery type if needed.",
                )
            )

        return issues
