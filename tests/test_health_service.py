from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bdayblaze.domain.models import (
    AnnouncementSurfaceSettings,
    GuildSettings,
    RuntimeStatus,
    SchedulerMetrics,
)
from bdayblaze.services.health_service import HealthService


class FakeRepository:
    def __init__(self) -> None:
        self.settings = replace(
            GuildSettings.default(1),
            announcements_enabled=True,
            anniversary_enabled=True,
        )
        self.surfaces = {
            "birthday_announcement": AnnouncementSurfaceSettings(
                guild_id=1,
                surface_kind="birthday_announcement",
                channel_id=123,
            ),
        }

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        assert guild_id == 1
        return self.settings

    async def list_guild_announcement_surfaces(
        self,
        guild_id: int,
    ) -> dict[str, AnnouncementSurfaceSettings]:
        assert guild_id == 1
        return self.surfaces

    async def list_recurring_celebrations(self, guild_id: int, *, limit: int) -> list[object]:
        assert guild_id == 1
        assert limit == 20
        return []

    async def fetch_server_anniversary(self, guild_id: int) -> None:
        assert guild_id == 1
        return None

    async def fetch_scheduler_backlog(
        self,
        now_utc: datetime,
        stale_window: object,
    ) -> object:
        return SimpleNamespace(
            oldest_due_birthday_utc=None,
            oldest_due_anniversary_utc=None,
            oldest_due_recurring_utc=None,
            oldest_due_role_removal_utc=None,
            oldest_due_event_utc=None,
            stale_processing_count=0,
        )

    async def list_recent_delivery_issues(
        self,
        guild_id: int,
        *,
        since_utc: datetime,
        limit: int,
    ) -> list[object]:
        assert guild_id == 1
        assert limit == 5
        return []


class FakeGuild:
    def __init__(self) -> None:
        self.id = 1
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=SimpleNamespace(),
        )

    def get_channel(self, channel_id: int) -> None:
        assert channel_id == 123
        return None

    def get_role(self, role_id: int) -> None:
        return None


def _runtime_status() -> RuntimeStatus:
    return RuntimeStatus(process_started_at_utc=datetime.now(UTC))


@pytest.mark.asyncio
async def test_health_service_reports_compact_live_route_and_source_notes() -> None:
    repository = FakeRepository()
    metrics = SchedulerMetrics(
        last_iteration_at_utc=datetime.now(UTC),
        recovery_completed=True,
    )
    service = HealthService(
        repository,  # type: ignore[arg-type]
        metrics,
        runtime_status=_runtime_status(),
        recovery_grace_hours=6,
        scheduler_max_sleep_seconds=60,
    )

    issues = await service.inspect_guild(FakeGuild())  # type: ignore[arg-type]

    actions = "\n".join(issue.action for issue in issues)

    assert (
        "Route: <#123> (inherits birthday default). "
        "Route source: inherits birthday default."
    ) in actions
    assert "Route: <#123> (custom). Route source: custom." in actions


@pytest.mark.asyncio
async def test_health_service_reports_compact_media_state_for_invalid_inherited_surfaces(
) -> None:
    repository = FakeRepository()
    repository.surfaces["birthday_announcement"] = AnnouncementSurfaceSettings(
        guild_id=1,
        surface_kind="birthday_announcement",
        channel_id=123,
        image_url="https://example.com/manual.pdf",
    )
    metrics = SchedulerMetrics(
        last_iteration_at_utc=datetime.now(UTC),
        recovery_completed=True,
    )
    service = HealthService(
        repository,  # type: ignore[arg-type]
        metrics,
        runtime_status=_runtime_status(),
        recovery_grace_hours=6,
        scheduler_max_sleep_seconds=60,
    )

    issues = await service.inspect_guild(FakeGuild())  # type: ignore[arg-type]
    actions = "\n".join(issue.action for issue in issues)

    assert "Image: unsupported file (custom, needs attention)." in actions
    assert "Media source: mixed (custom / not set)." in actions
    assert "Image: unsupported file (inherits birthday default, needs attention)." in actions


@pytest.mark.asyncio
async def test_health_service_distinguishes_alive_but_failing_scheduler() -> None:
    repository = FakeRepository()
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_activity_at_utc=datetime.now(UTC),
        last_success_at_utc=datetime.now(UTC) - timedelta(minutes=20),
        last_error_code="TimeoutError",
    )
    service = HealthService(
        repository,  # type: ignore[arg-type]
        metrics,
        runtime_status=_runtime_status(),
        recovery_grace_hours=6,
        scheduler_max_sleep_seconds=60,
    )

    issues = await service.inspect_guild(FakeGuild())  # type: ignore[arg-type]

    assert any(issue.code == "scheduler_failing" for issue in issues)
    assert not any(issue.code == "scheduler_stalled" for issue in issues)


@pytest.mark.asyncio
async def test_health_service_reports_stuck_processing_events_accurately() -> None:
    repository = FakeRepository()

    async def fetch_scheduler_backlog(now_utc: datetime, stale_window: object) -> object:
        return SimpleNamespace(
            oldest_due_birthday_utc=None,
            oldest_due_anniversary_utc=None,
            oldest_due_recurring_utc=None,
            oldest_due_role_removal_utc=None,
            oldest_due_event_utc=None,
            stale_processing_count=3,
        )

    repository.fetch_scheduler_backlog = fetch_scheduler_backlog  # type: ignore[method-assign]
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_activity_at_utc=datetime.now(UTC),
        last_success_at_utc=datetime.now(UTC),
    )
    service = HealthService(
        repository,  # type: ignore[arg-type]
        metrics,
        runtime_status=_runtime_status(),
        recovery_grace_hours=6,
        scheduler_max_sleep_seconds=60,
    )

    issues = await service.inspect_guild(FakeGuild())  # type: ignore[arg-type]

    stuck_issue = next(issue for issue in issues if issue.code == "stale_processing_events")
    assert "stuck in processing" in stuck_issue.summary
