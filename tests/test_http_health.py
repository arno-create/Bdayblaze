from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
