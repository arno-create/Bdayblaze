from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from json import dumps

from bdayblaze.domain.models import SchedulerMetrics
from bdayblaze.logging import get_logger


@dataclass(slots=True)
class HttpHealthServer:
    metrics: SchedulerMetrics
    host: str
    port: int
    _server: asyncio.base_events.Server | None = field(init=False, default=None)
    _logger: object = field(init=False)

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
            status_code = "200 OK"
            payload = self._build_payload()
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

    def _build_payload(self) -> dict[str, str | int | bool | None]:
        return {
            "status": "ok",
            "utc": datetime.now(UTC).isoformat(),
            "scheduler_recovery_completed": self.metrics.recovery_completed,
            "scheduler_iterations": self.metrics.iterations,
            "scheduler_last_iteration_at_utc": (
                self.metrics.last_iteration_at_utc.isoformat()
                if self.metrics.last_iteration_at_utc is not None
                else None
            ),
            "scheduler_last_error_code": self.metrics.last_error_code,
        }
