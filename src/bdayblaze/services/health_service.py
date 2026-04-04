from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import discord

from bdayblaze.domain.announcement_surfaces import (
    resolve_announcement_surface,
)
from bdayblaze.domain.birthday_logic import validate_timezone
from bdayblaze.domain.models import (
    AnnouncementSurfaceKind,
    HealthIssue,
    ResolvedAnnouncementSurface,
    RuntimeStatus,
    SchedulerMetrics,
)
from bdayblaze.domain.operator_summary import (
    media_health_line,
    media_line,
    media_source_line,
    route_line,
    route_source_line,
)
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.diagnostics import (
    build_channel_diagnostics,
    build_event_content_diagnostics,
    build_presentation_diagnostics,
    build_role_diagnostics,
    build_studio_content_diagnostics,
    describe_delivery_error_code,
)


class HealthService:
    def __init__(
        self,
        repository: PostgresRepository,
        metrics: SchedulerMetrics,
        *,
        runtime_status: RuntimeStatus,
        recovery_grace_hours: int,
        scheduler_max_sleep_seconds: int,
    ) -> None:
        self._repository = repository
        self._metrics = metrics
        self._runtime_status = runtime_status
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
                        "Run /birthdayadmin setup to configure timezone, channels, "
                        "and delivery rules."
                    ),
                )
            )
        else:
            announcement_surfaces = await self._repository.list_guild_announcement_surfaces(
                guild.id
            )
            birthday_surface = resolve_announcement_surface(
                guild.id,
                "birthday_announcement",
                announcement_surfaces,
            )
            try:
                validate_timezone(settings.default_timezone)
            except ValueError:
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="invalid_timezone",
                        summary="The saved default timezone is invalid.",
                        action="Open /birthdayadmin setup and save a valid IANA timezone.",
                    )
                )

            for diagnostic in build_presentation_diagnostics(
                birthday_surface.presentation(settings)
            ):
                issues.append(
                    HealthIssue(
                        severity=diagnostic.severity,
                        code=diagnostic.code,
                        summary=diagnostic.summary,
                        action=(
                            diagnostic.action or "Review the saved celebration media settings."
                        )
                        + f" {_surface_media_note(birthday_surface)}",
                    )
                )
            for diagnostic in build_studio_content_diagnostics(settings):
                issues.append(
                    HealthIssue(
                        severity=diagnostic.severity,
                        code=diagnostic.code,
                        summary=diagnostic.summary,
                        action=diagnostic.action or "Review the saved Celebration Studio content.",
                    )
                )

            for diagnostic in build_channel_diagnostics(
                guild,
                channel_id=(
                    birthday_surface.channel.effective_value
                    if settings.announcements_enabled
                    else None
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
                            )
                            + f" {_surface_channel_note(birthday_surface)}",
                        )
                    )

            anniversary_surface = resolve_announcement_surface(
                guild.id,
                "anniversary",
                announcement_surfaces,
            )
            for diagnostic in build_presentation_diagnostics(
                anniversary_surface.presentation(settings)
            ):
                issues.append(
                    HealthIssue(
                        severity=diagnostic.severity,
                        code=f"anniversary_{diagnostic.code}",
                        summary=diagnostic.summary,
                        action=(
                            diagnostic.action
                            or "Review the saved anniversary surface media settings."
                        )
                        + f" {_surface_media_note(anniversary_surface)}",
                    )
                )
            for diagnostic in build_channel_diagnostics(
                guild,
                channel_id=(
                    anniversary_surface.channel.effective_value
                    if settings.anniversary_enabled
                    else None
                ),
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
                            )
                            + f" {_surface_channel_note(anniversary_surface)}",
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
            for diagnostic in build_channel_diagnostics(
                guild,
                channel_id=settings.studio_audit_channel_id,
                label="studio audit",
            ):
                if settings.studio_audit_channel_id is not None:
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=f"studio_audit_{diagnostic.code}",
                            summary=diagnostic.summary,
                            action=(
                                diagnostic.action
                                or "Review the configured Studio audit channel."
                            ),
                        )
                    )

            recurring_events = await self._repository.list_recurring_celebrations(
                guild.id,
                limit=20,
            )
            for celebration in recurring_events:
                for diagnostic in build_event_content_diagnostics(celebration):
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=f"recurring_event_{celebration.id}_{diagnostic.code}",
                            summary=(
                                f"Recurring event '{celebration.name}' is blocked: "
                                f"{diagnostic.summary}"
                            ),
                            action=diagnostic.action or "Review the recurring event content.",
                        )
                    )
                if not celebration.enabled:
                    continue
                surface_kind: AnnouncementSurfaceKind = (
                    "server_anniversary"
                    if celebration.celebration_kind == "server_anniversary"
                    else "recurring_event"
                )
                resolved_surface = resolve_announcement_surface(
                    guild.id,
                    surface_kind,
                    announcement_surfaces,
                    event_channel_id=celebration.channel_id,
                )
                for diagnostic in build_presentation_diagnostics(
                    resolved_surface.presentation(settings)
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
                                diagnostic.action
                                or "Review the recurring event surface media settings."
                            )
                            + f" {_surface_media_note(resolved_surface)}",
                        )
                    )
                for diagnostic in build_channel_diagnostics(
                    guild,
                    channel_id=resolved_surface.channel.effective_value,
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
                                (
                                    diagnostic.action
                                    or "Review the recurring event channel override."
                                )
                                + f" {_surface_channel_note(resolved_surface)}"
                            ),
                        )
                    )

            server_anniversary = await self._repository.fetch_server_anniversary(guild.id)
            if server_anniversary is not None:
                for diagnostic in build_event_content_diagnostics(server_anniversary):
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=f"server_anniversary_{diagnostic.code}",
                            summary=f"Server anniversary is blocked: {diagnostic.summary}",
                            action=diagnostic.action or "Review the server-anniversary content.",
                        )
                    )
            if server_anniversary is not None and server_anniversary.enabled:
                resolved_server_surface = resolve_announcement_surface(
                    guild.id,
                    "server_anniversary",
                    announcement_surfaces,
                    event_channel_id=server_anniversary.channel_id,
                )
                for diagnostic in build_presentation_diagnostics(
                    resolved_server_surface.presentation(settings)
                ):
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=f"server_anniversary_{diagnostic.code}",
                            summary=f"Server anniversary is blocked: {diagnostic.summary}",
                            action=(
                                diagnostic.action
                                or "Review the server-anniversary surface media settings."
                            )
                            + f" {_surface_media_note(resolved_server_surface)}",
                        )
                    )
                for diagnostic in build_channel_diagnostics(
                    guild,
                    channel_id=resolved_server_surface.channel.effective_value,
                    label="server anniversary",
                ):
                    issues.append(
                        HealthIssue(
                            severity=diagnostic.severity,
                            code=f"server_anniversary_{diagnostic.code}",
                            summary=f"Server anniversary is blocked: {diagnostic.summary}",
                            action=(
                                (
                                    diagnostic.action
                                    or "Review the server-anniversary channel override."
                                )
                                + f" {_surface_channel_note(resolved_server_surface)}"
                            ),
                        )
                    )

        now_utc = datetime.now(UTC)
        stale_window = timedelta(seconds=max(self._scheduler_max_sleep_seconds * 2, 600))
        backlog = await self._repository.fetch_scheduler_backlog(now_utc, stale_window)

        oldest_due_source = _oldest_due_source(backlog)
        if oldest_due_source is not None:
            oldest_due_label, oldest_due = oldest_due_source
            lag = now_utc - oldest_due
            if lag > timedelta(hours=self._recovery_grace_hours):
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="scheduler_recovery_window_exceeded",
                        summary=(
                            "Scheduler backlog for "
                            f"{oldest_due_label} is older than the configured recovery window."
                        ),
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
                        summary=f"Scheduler work is running behind on {oldest_due_label}.",
                        action="Keep the bot online and re-check after the backlog clears.",
                    )
                )

        if backlog.stale_processing_count > 0:
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="stale_processing_events",
                    summary=(
                        f"{backlog.stale_processing_count} celebration event(s) are stuck in "
                        "processing and waiting to be reclaimed."
                    ),
                    action="Review recent scheduler failures and confirm stuck deliveries clear.",
                )
            )

        if not self._metrics.recovery_completed:
            issues.append(
                HealthIssue(
                    severity=(
                        "error"
                        if self._runtime_status.scheduler_recovery_failed_at_utc is not None
                        else "info"
                    ),
                    code=(
                        "scheduler_recovery_failed"
                        if self._runtime_status.scheduler_recovery_failed_at_utc is not None
                        else "recovery_incomplete"
                    ),
                    summary=(
                        "Scheduler startup recovery failed."
                        if self._runtime_status.scheduler_recovery_failed_at_utc is not None
                        else (
                            "Scheduler startup recovery is still running."
                            if self._runtime_status.scheduler_recovery_started_at_utc is not None
                            else "Scheduler startup recovery has not started yet."
                        )
                    ),
                    action=(
                        "Review startup logs, then restart the bot after the failure is fixed."
                        if self._runtime_status.scheduler_recovery_failed_at_utc is not None
                        else "Re-run /birthdayadmin health after the bot has been "
                        "online for a minute."
                    ),
                )
            )

        last_activity = self._metrics.last_activity_at_utc or self._metrics.last_iteration_at_utc
        if self._metrics.recovery_completed:
            if last_activity is None:
                issues.append(
                    HealthIssue(
                        severity="warning",
                        code="scheduler_not_started",
                        summary="Scheduler has not recorded loop activity yet.",
                        action="Review startup logs and verify the scheduler runner started.",
                    )
                )
            elif now_utc - last_activity > stale_window:
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="scheduler_stalled",
                        summary="Scheduler loop heartbeat is stale.",
                        action="Restart the bot process and verify database connectivity.",
                    )
                )
            elif (
                self._metrics.last_error_code is not None
                and (
                    self._metrics.last_success_at_utc is None
                    or now_utc - self._metrics.last_success_at_utc > stale_window
                )
            ):
                issues.append(
                    HealthIssue(
                        severity="warning",
                        code="scheduler_failing",
                        summary=(
                            "Scheduler loop is active but has not completed successfully recently."
                        ),
                        action=(
                            "Review scheduler logs for repeated failures and confirm backlog stops "
                            "growing."
                        ),
                    )
                )

        recent_issues = await self._repository.list_recent_delivery_issues(
            guild.id,
            since_utc=now_utc - timedelta(days=7),
            limit=5,
        )
        for recent_issue in recent_issues:
            assert recent_issue.last_error_code is not None
            summary, action = describe_delivery_error_code(
                event_kind=recent_issue.event_kind,
                error_code=recent_issue.last_error_code,
            )
            issues.append(
                HealthIssue(
                    severity="info",
                    code=f"recent_{recent_issue.last_error_code}",
                    summary=summary,
                    action=action,
                )
            )

        return issues


def _oldest_due_source(backlog: object) -> tuple[str, datetime] | None:
    candidates = [
        ("birthdays", getattr(backlog, "oldest_due_birthday_utc", None)),
        ("anniversaries", getattr(backlog, "oldest_due_anniversary_utc", None)),
        ("recurring events", getattr(backlog, "oldest_due_recurring_utc", None)),
        ("role removals", getattr(backlog, "oldest_due_role_removal_utc", None)),
        ("pending event delivery", getattr(backlog, "oldest_due_event_utc", None)),
    ]
    due_candidates = [
        (label, due_at)
        for label, due_at in candidates
        if isinstance(due_at, datetime)
    ]
    if not due_candidates:
        return None
    return min(due_candidates, key=lambda candidate: candidate[1])


def _surface_channel_note(surface: ResolvedAnnouncementSurface) -> str:
    return (
        f"{route_line(surface.channel, surface_kind=surface.surface_kind)}. "
        f"{route_source_line(surface.channel, surface_kind=surface.surface_kind)}."
    )


def _surface_media_note(surface: ResolvedAnnouncementSurface) -> str:
    return (
        f"{_surface_media_field_note(surface, field_name='image')} "
        f"{_surface_media_field_note(surface, field_name='thumbnail')} "
        f"{media_health_line(surface)}. {media_source_line(surface)}."
    )


def _surface_media_field_note(
    surface: ResolvedAnnouncementSurface,
    *,
    field_name: str,
) -> str:
    field = surface.image if field_name == "image" else surface.thumbnail
    label: Literal["image", "thumbnail"] = "image" if field_name == "image" else "thumbnail"
    return (
        media_line(
            field,
            label=label,
            surface_kind=surface.surface_kind,
        )
        + "."
    )
