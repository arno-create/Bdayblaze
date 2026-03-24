from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bdayblaze.domain.models import SchedulerMetrics
from bdayblaze.http_server import HttpHealthServer


def test_http_health_reports_starting_before_recovery_finishes() -> None:
    server = HttpHealthServer(
        metrics=SchedulerMetrics(),
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response()

    assert status_code == "200 OK"
    assert payload["status"] == "starting"


def test_http_health_reports_ok_for_fresh_scheduler_heartbeat() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC),
        iterations=4,
    )
    server = HttpHealthServer(
        metrics=metrics,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response()

    assert status_code == "200 OK"
    assert payload["status"] == "ok"


def test_http_health_reports_error_for_stale_scheduler_heartbeat() -> None:
    metrics = SchedulerMetrics(
        recovery_completed=True,
        last_iteration_at_utc=datetime.now(UTC) - timedelta(minutes=20),
        iterations=4,
    )
    server = HttpHealthServer(
        metrics=metrics,
        host="127.0.0.1",
        port=8080,
        scheduler_max_sleep_seconds=300,
    )

    status_code, payload = server._build_response()

    assert status_code == "503 Service Unavailable"
    assert payload["status"] == "error"
