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

        if path == "/":
            status_code = "200 OK"
            body = self._build_root_page().encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path in {"/health", "/healthz", "/livez", "/readyz"}:
            status_code, payload = self._build_response(path)
            body = dumps(payload).encode("utf-8")
            content_type = "application/json"
        else:
            status_code = "404 Not Found"
            body = dumps({"status": "not_found"}).encode("utf-8")
            content_type = "application/json"
        writer.write(
            (
                f"HTTP/1.1 {status_code}\r\n"
                f"Content-Type: {content_type}\r\n"
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
            "503 Service Unavailable"
            if detail["status"] in {"error", "degraded"}
            else "200 OK",
            detail,
        )

    def _detail_payload(self) -> dict[str, str | int | bool | None]:
        stale_window = timedelta(seconds=max(self.scheduler_max_sleep_seconds * 2, 600))
        now_utc = datetime.now(UTC)
        last_iteration = self.metrics.last_iteration_at_utc
        last_activity = self.metrics.last_activity_at_utc or last_iteration
        last_success = self.metrics.last_success_at_utc
        heartbeat_stale = last_activity is None or now_utc - last_activity > stale_window
        success_stale = last_success is None or now_utc - last_success > stale_window

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
        elif self.metrics.last_error_code is not None and success_stale:
            status = "degraded"
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
            "scheduler_last_activity_at_utc": (
                last_activity.isoformat() if last_activity is not None else None
            ),
            "scheduler_last_success_at_utc": (
                last_success.isoformat() if last_success is not None else None
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

    def _build_root_page(self) -> str:
        return """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Bdayblaze Runtime</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #fbf5eb;
        --ink: #24190e;
        --muted: #6d5842;
        --line: rgba(70, 46, 24, 0.16);
        --accent: #c7662d;
      }

      * { box-sizing: border-box; }

      body {
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top left, rgba(244, 178, 78, 0.22), transparent 34%),
          linear-gradient(180deg, #fff9f0 0%, var(--bg) 100%);
        color: var(--ink);
        font-family: "Segoe UI", system-ui, sans-serif;
      }

      main {
        width: min(780px, calc(100% - 2rem));
        margin: 0 auto;
        padding: 4rem 0;
      }

      .panel {
        border: 1px solid var(--line);
        border-radius: 24px;
        background: rgba(255, 250, 243, 0.9);
        box-shadow: 0 24px 80px rgba(61, 34, 10, 0.08);
        padding: 1.5rem;
      }

      h1 {
        margin: 0 0 0.75rem;
        font-size: clamp(2rem, 5vw, 3rem);
        line-height: 1;
      }

      p, li {
        color: var(--muted);
        line-height: 1.6;
      }

      ul {
        padding-left: 1.25rem;
        margin: 1rem 0 0;
      }

      a {
        color: var(--accent);
        text-decoration: none;
      }
    </style>
  </head>
  <body>
    <main>
      <section class="panel">
        <h1>Bdayblaze bot runtime</h1>
        <p>
          This Render service runs the Discord bot and exposes health endpoints. It is not the
          public static website.
        </p>
        <p>
          GitHub Pages should publish the repository root so the static landing page and its
          assets are served from the same source of truth.
        </p>
        <ul>
          <li><a href="/livez">/livez</a> for basic process liveness.</li>
          <li><a href="/readyz">/readyz</a> for readiness and scheduler health.</li>
          <li><a href="/healthz">/healthz</a> for detailed runtime state.</li>
        </ul>
      </section>
    </main>
  </body>
</html>
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
