from __future__ import annotations

from datetime import UTC, datetime, timedelta
from json import loads
from types import SimpleNamespace

import pytest

from bdayblaze.domain.models import RuntimeStatus, SchedulerMetrics
from bdayblaze.http_server import HttpHealthServer


def _runtime_status() -> RuntimeStatus:
    return RuntimeStatus(
        process_started_at_utc=datetime.now(UTC) - timedelta(seconds=5),
    )


def test_http_health_reports_starting_before_recovery_finishes() -> None:
    server = HttpHealthServer(
        metrics=SchedulerMetrics(),
        runtime_status=_runtime_status(),
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response()

    assert status_code == "200 OK"
    assert payload["status"] == "starting"


def test_http_health_root_page_explains_runtime_surface() -> None:
    server = HttpHealthServer(
        metrics=SchedulerMetrics(),
        runtime_status=_runtime_status(),
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    page = server._build_root_page()

    assert "Bdayblaze bot runtime" in page
    assert "repository root" in page
    assert "/readyz" in page


def test_http_health_reports_ok_for_fresh_scheduler_heartbeat() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC),
        iterations=4,
    )
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=metrics,
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response()

    assert status_code == "200 OK"
    assert payload["status"] == "ready"


def test_http_health_reports_error_for_stale_scheduler_heartbeat() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC) - timedelta(minutes=20),
        iterations=4,
    )
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=metrics,
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response()

    assert status_code == "503 Service Unavailable"
    assert payload["status"] == "error"


def test_http_health_reports_readiness_on_readyz() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC),
        iterations=2,
    )
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=metrics,
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response("/readyz")

    assert status_code == "200 OK"
    assert payload["status"] == "ready"


def test_http_health_reports_liveness_on_livez() -> None:
    server = HttpHealthServer(
        metrics=SchedulerMetrics(),
        runtime_status=_runtime_status(),
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response("/livez")

    assert status_code == "200 OK"
    assert payload["status"] == "live"


def test_http_health_reports_error_when_scheduler_recovery_failed() -> None:
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    runtime_status.scheduler_recovery_failed_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=SchedulerMetrics(recovery_completed=False),
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response("/healthz")

    assert status_code == "503 Service Unavailable"
    assert payload["status"] == "error"
    assert payload["scheduler_recovery_completed"] is False


def test_http_health_reports_degraded_when_scheduler_is_alive_but_not_succeeding() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_activity_at_utc=datetime.now(UTC),
        last_success_at_utc=datetime.now(UTC) - timedelta(minutes=20),
        last_error_code="TimeoutError",
    )
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=metrics,
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response("/healthz")

    assert status_code == "503 Service Unavailable"
    assert payload["status"] == "degraded"
    assert payload["scheduler_last_activity_at_utc"] is not None


class FakeVoteService:
    def __init__(
        self,
        *,
        snapshot: dict[str, object],
        webhook_status: int = 200,
        webhook_body: dict[str, object] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._webhook_status = webhook_status
        self._webhook_body = webhook_body or {"status": "processed"}
        self.webhook_calls: list[tuple[dict[str, str], bytes]] = []

    def diagnostics_snapshot(self) -> dict[str, object]:
        return dict(self._snapshot)

    async def handle_webhook(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        now_utc: datetime,
    ) -> SimpleNamespace:
        self.webhook_calls.append((headers, raw_body))
        return SimpleNamespace(
            http_status=self._webhook_status,
            payload=self._webhook_body,
        )


def test_http_health_includes_disabled_topgg_block_without_degrading_readiness() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC),
        iterations=2,
    )
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=metrics,
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
        vote_service=FakeVoteService(
            snapshot={
                "enabled": False,
                "configuration_state": "disabled",
                "configuration_message": "Top.gg vote bonus is disabled.",
                "webhook_mode": None,
                "storage_ready": True,
                "storage_backend": "postgres",
                "refresh_available": False,
                "refresh_cooldown_seconds": 60,
                "timing_source": None,
            }
        ),
    )

    status_code, payload = server._build_response("/readyz")

    assert status_code == "200 OK"
    assert payload["status"] == "ready"
    assert payload["topgg"]["configuration_state"] == "disabled"
    assert payload["topgg"]["public_routes_ready"] is True


def test_http_health_degrades_when_topgg_is_enabled_but_misconfigured() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC),
        iterations=2,
    )
    runtime_status = _runtime_status()
    runtime_status.bot_ready_at_utc = datetime.now(UTC)
    server = HttpHealthServer(
        metrics=metrics,
        runtime_status=runtime_status,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
        vote_service=FakeVoteService(
            snapshot={
                "enabled": True,
                "configuration_state": "misconfigured",
                "configuration_message": "Missing webhook secret.",
                "webhook_mode": None,
                "storage_ready": True,
                "storage_backend": "postgres",
                "refresh_available": False,
                "refresh_cooldown_seconds": 60,
                "timing_source": None,
            }
        ),
    )

    status_code, payload = server._build_response("/readyz")

    assert status_code == "503 Service Unavailable"
    assert payload["status"] == "degraded"
    assert payload["topgg"]["configuration_state"] == "misconfigured"


@pytest.mark.asyncio
async def test_topgg_webhook_route_returns_truthful_503_when_disabled() -> None:
    server = HttpHealthServer(
        metrics=SchedulerMetrics(),
        runtime_status=_runtime_status(),
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
        vote_service=FakeVoteService(
            snapshot={
                "enabled": False,
                "configuration_state": "disabled",
                "configuration_message": "Top.gg vote bonus is disabled.",
                "webhook_mode": None,
                "storage_ready": True,
                "storage_backend": "postgres",
                "refresh_available": False,
                "refresh_cooldown_seconds": 60,
                "timing_source": None,
            },
            webhook_status=503,
            webhook_body={
                "status": "disabled",
                "message": "Top.gg vote bonus is intentionally disabled.",
            },
        ),
    )

    status_code, body, content_type = await server._route_request(
        method="POST",
        path="/topgg/webhook",
        headers={"content-type": "application/json"},
        body=b"{}",
    )

    assert status_code == "503 Service Unavailable"
    assert content_type == "application/json"
    assert loads(body)["status"] == "disabled"
