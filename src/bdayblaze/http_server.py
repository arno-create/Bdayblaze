from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from json import dumps
from typing import Any

from bdayblaze.domain.models import RuntimeStatus, SchedulerMetrics
from bdayblaze.logging import get_logger


@dataclass(slots=True)
class HttpHealthServer:
    metrics: SchedulerMetrics
    runtime_status: RuntimeStatus
    host: str
    port: int
    scheduler_max_sleep_seconds: int
    _server: asyncio.base_events.Server | None = field(init=False, default=None)
    _logger: Any = field(init=False)

    def __post_init__(self) -> None:
        self._logger = get_logger(component="http_health")

    async def start(self) -> None:
        try:
            self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        except Exception:
            self.runtime_status.health_server_failed_at_utc = datetime.now(UTC)
            raise
        self.runtime_status.health_server_started_at_utc = datetime.now(UTC)
        self._logger.info("http_health_server_started", host=self.host, port=self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        except TimeoutError:
            writer.close()
            await writer.wait_closed()
            return

        path = "/"
        try:
            parts = request_line.decode("ascii", errors="ignore").strip().split()
            if len(parts) >= 2:
                path = parts[1]
        finally:
            while True:
                line = await reader.readline()
                if not line or line in {b"\r\n", b"\n"}:
                    break

        if path in {"/", "/health", "/healthz", "/livez", "/readyz"}:
            status_code, payload = self._build_response(path)
        else:
            status_code = "404 Not Found"
            payload = {"status": "not_found"}

        body = dumps(payload).encode("utf-8")
        writer.write(
            (
                f"HTTP/1.1 {status_code}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
        )
        writer.write(body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _build_response(
        self,
        path: str = "/healthz",
    ) -> tuple[str, dict[str, str | int | bool | None]]:
        if path == "/livez":
            return (
                "200 OK",
                {
                    "status": "live",
                    "phase": self.runtime_status.startup_phase,
                    "utc": datetime.now(UTC).isoformat(),
                },
            )
        detail = self._detail_payload()
        if path == "/readyz":
            return (
                "200 OK" if detail["status"] == "ready" else "503 Service Unavailable",
                detail,
            )
        return (
            "503 Service Unavailable" if detail["status"] == "error" else "200 OK",
            detail,
        )

    def _detail_payload(self) -> dict[str, str | int | bool | None]:
        stale_window = timedelta(seconds=max(self.scheduler_max_sleep_seconds * 2, 600))
        now_utc = datetime.now(UTC)
        last_iteration = self.metrics.last_iteration_at_utc
        heartbeat_stale = last_iteration is None or now_utc - last_iteration > stale_window

        if self.runtime_status.migrations_failed_at_utc is not None:
            status = "error"
        elif self.runtime_status.health_server_failed_at_utc is not None:
            status = "error"
        elif self.runtime_status.scheduler_recovery_failed_at_utc is not None:
            status = "error"
        elif self.runtime_status.unexpected_shutdown_at_utc is not None:
            status = "error"
        elif self.runtime_status.bot_ready_at_utc is None:
            status = "starting"
        elif not self.metrics.recovery_completed:
            status = "starting"
        elif heartbeat_stale:
            status = "error"
        else:
            status = "ready"

        return {
            "status": status,
            "phase": self.runtime_status.startup_phase,
            "utc": now_utc.isoformat(),
            "bot_ready": self.runtime_status.bot_ready_at_utc is not None,
            "scheduler_recovery_completed": self.metrics.recovery_completed,
            "scheduler_iterations": self.metrics.iterations,
            "scheduler_last_iteration_at_utc": (
                last_iteration.isoformat() if last_iteration is not None else None
            ),
            "scheduler_last_error_code": self.metrics.last_error_code,
            "scheduler_last_claimed_events": self.metrics.last_claimed_events,
            "process_started_at_utc": self.runtime_status.process_started_at_utc.isoformat(),
            "db_pool_ready_at_utc": _iso(self.runtime_status.db_pool_ready_at_utc),
            "migrations_started_at_utc": _iso(self.runtime_status.migrations_started_at_utc),
            "migrations_completed_at_utc": _iso(self.runtime_status.migrations_completed_at_utc),
            "migrations_failed_at_utc": _iso(self.runtime_status.migrations_failed_at_utc),
            "health_server_started_at_utc": _iso(self.runtime_status.health_server_started_at_utc),
            "health_server_failed_at_utc": _iso(self.runtime_status.health_server_failed_at_utc),
            "bot_login_started_at_utc": _iso(self.runtime_status.bot_login_started_at_utc),
            "bot_ready_at_utc": _iso(self.runtime_status.bot_ready_at_utc),
            "scheduler_recovery_started_at_utc": _iso(
                self.runtime_status.scheduler_recovery_started_at_utc
            ),
            "scheduler_recovery_completed_at_utc": _iso(
                self.runtime_status.scheduler_recovery_completed_at_utc
            ),
            "scheduler_recovery_failed_at_utc": _iso(
                self.runtime_status.scheduler_recovery_failed_at_utc
            ),
            "unexpected_shutdown_at_utc": _iso(self.runtime_status.unexpected_shutdown_at_utc),
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
