from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from json import dumps
from typing import Any

from bdayblaze.domain.models import SchedulerMetrics
from bdayblaze.logging import get_logger


@dataclass(slots=True)
class HttpHealthServer:
    metrics: SchedulerMetrics
    host: str
    port: int
    scheduler_max_sleep_seconds: int
    _server: asyncio.base_events.Server | None = field(init=False, default=None)
    _logger: Any = field(init=False)

    def __post_init__(self) -> None:
        self._logger = get_logger(component="http_health")

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
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

        if path in {"/", "/health", "/healthz"}:
            status_code, payload = self._build_response()
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

    def _build_response(self) -> tuple[str, dict[str, str | int | bool | None]]:
        stale_window = timedelta(seconds=max(self.scheduler_max_sleep_seconds * 2, 600))
        now_utc = datetime.now(UTC)
        last_iteration = self.metrics.last_iteration_at_utc
        heartbeat_stale = last_iteration is None or now_utc - last_iteration > stale_window

        if not self.metrics.recovery_completed:
            status = "error" if heartbeat_stale and last_iteration is not None else "starting"
        elif heartbeat_stale:
            status = "error"
        else:
            status = "ok"

        return (
            "503 Service Unavailable" if status == "error" else "200 OK",
            {
                "status": status,
                "utc": now_utc.isoformat(),
                "scheduler_recovery_completed": self.metrics.recovery_completed,
                "scheduler_iterations": self.metrics.iterations,
                "scheduler_last_iteration_at_utc": (
                    last_iteration.isoformat() if last_iteration is not None else None
                ),
                "scheduler_last_error_code": self.metrics.last_error_code,
            },
        )
