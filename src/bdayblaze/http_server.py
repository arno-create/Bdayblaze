from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from json import dumps
from typing import Any

from bdayblaze.domain.models import RuntimeStatus, SchedulerMetrics
from bdayblaze.logging import get_logger
from bdayblaze.services.vote_service import VoteService

_MAX_REQUEST_BODY_BYTES = 64 * 1024
_MAX_REQUEST_HEADER_BYTES = 16 * 1024
_MAX_REQUEST_HEADER_LINE_BYTES = 8 * 1024
_MAX_REQUEST_HEADER_LINES = 100
_REQUEST_LINE_TIMEOUT_SECONDS = 5
_HEADER_READ_TIMEOUT_SECONDS = 5
_BODY_READ_TIMEOUT_SECONDS = 5


@dataclass(slots=True)
class HttpHealthServer:
    metrics: SchedulerMetrics
    runtime_status: RuntimeStatus
    host: str
    port: int
    scheduler_max_sleep_seconds: int
    vote_service: VoteService | None = None
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
            request_line = await asyncio.wait_for(
                reader.readline(),
                timeout=_REQUEST_LINE_TIMEOUT_SECONDS,
            )
            if not request_line:
                raise _BadRequest
            parts = request_line.decode("ascii", errors="ignore").strip().split()
            if len(parts) < 2:
                raise _BadRequest
            method = parts[0].upper()
            raw_path = parts[1]
            headers = await self._read_headers(reader)
            body = await self._read_body(reader, headers)
            path = raw_path.split("?", 1)[0] or "/"
            status_code, response_body, content_type = await self._route_request(
                method=method,
                path=path,
                headers=headers,
                body=body,
            )
        except _BodyTooLarge:
            status_code = "413 Payload Too Large"
            response_body = dumps({"status": "payload_too_large"}).encode("utf-8")
            content_type = "application/json"
        except _RequestTimeout:
            status_code = "408 Request Timeout"
            response_body = dumps({"status": "request_timeout"}).encode("utf-8")
            content_type = "application/json"
        except _BadRequest:
            status_code = "400 Bad Request"
            response_body = dumps({"status": "bad_request"}).encode("utf-8")
            content_type = "application/json"
        except Exception:
            self._logger.exception("http_request_failed")
            status_code = "500 Internal Server Error"
            response_body = dumps({"status": "internal_error"}).encode("utf-8")
            content_type = "application/json"

        writer.write(
            (
                f"HTTP/1.1 {status_code}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
        )
        writer.write(response_body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _route_request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[str, bytes, str]:
        if path == "/" and method == "GET":
            return (
                "200 OK",
                self._build_root_page().encode("utf-8"),
                "text/html; charset=utf-8",
            )
        if path in {"/health", "/healthz", "/livez", "/readyz"} and method == "GET":
            status_code, payload = self._build_response(path)
            return status_code, dumps(payload).encode("utf-8"), "application/json"
        if path == "/topgg/webhook":
            if method != "POST":
                return (
                    "405 Method Not Allowed",
                    dumps({"status": "method_not_allowed"}).encode("utf-8"),
                    "application/json",
                )
            if self.vote_service is None:
                return (
                    "503 Service Unavailable",
                    dumps(
                        {
                            "status": "disabled",
                            "message": "Top.gg vote bonus is unavailable in this runtime.",
                        }
                    ).encode("utf-8"),
                    "application/json",
                )
            result = await self.vote_service.handle_webhook(
                headers=headers,
                raw_body=body,
                now_utc=datetime.now(UTC),
            )
            return (
                _http_status_text(result.http_status),
                dumps(result.payload).encode("utf-8"),
                "application/json",
            )
        return (
            "404 Not Found",
            dumps({"status": "not_found"}).encode("utf-8"),
            "application/json",
        )

    def _build_response(
        self,
        path: str = "/healthz",
    ) -> tuple[str, dict[str, object]]:
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

    def _detail_payload(self) -> dict[str, object]:
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

        topgg = self._safe_topgg_diagnostics()
        if (
            topgg is not None
            and topgg["enabled"]
            and (
                topgg["configuration_state"] != "ready"
                or not topgg["storage_ready"]
                or not topgg["reminder_ready"]
                or not topgg["runtime_attached"]
                or not topgg["public_routes_ready"]
            )
            and status == "ready"
        ):
            status = "degraded"

        payload: dict[str, object] = {
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
        if topgg is not None:
            payload["topgg"] = topgg
        return payload

    async def _read_headers(self, reader: asyncio.StreamReader) -> dict[str, str]:
        headers: dict[str, str] = {}
        total_bytes = 0
        line_count = 0
        while True:
            try:
                line = await asyncio.wait_for(
                    reader.readline(),
                    timeout=_HEADER_READ_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                raise _RequestTimeout from exc
            if not line or line in {b"\r\n", b"\n"}:
                break
            line_count += 1
            total_bytes += len(line)
            if (
                len(line) > _MAX_REQUEST_HEADER_LINE_BYTES
                or line_count > _MAX_REQUEST_HEADER_LINES
                or total_bytes > _MAX_REQUEST_HEADER_BYTES
            ):
                raise _BadRequest
            decoded = line.decode("ascii", errors="ignore").strip()
            name, _, value = decoded.partition(":")
            if not name:
                continue
            headers[name.lower()] = value.strip()
        return headers

    async def _read_body(
        self,
        reader: asyncio.StreamReader,
        headers: dict[str, str],
    ) -> bytes:
        raw_content_length = headers.get("content-length", "0") or "0"
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise _BadRequest from exc
        if content_length < 0:
            raise _BadRequest
        if content_length == 0:
            return b""
        if content_length > _MAX_REQUEST_BODY_BYTES:
            raise _BodyTooLarge
        try:
            return await asyncio.wait_for(
                reader.readexactly(content_length),
                timeout=_BODY_READ_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise _RequestTimeout from exc
        except (EOFError, asyncio.IncompleteReadError) as exc:
            raise _BadRequest from exc

    def _safe_topgg_diagnostics(self) -> dict[str, object] | None:
        if self.vote_service is None:
            return None
        snapshot = self.vote_service.diagnostics_snapshot()
        return {
            "enabled": bool(snapshot.get("enabled")),
            "configuration_state": snapshot.get("configuration_state"),
            "configuration_message": snapshot.get("configuration_message"),
            "webhook_mode": snapshot.get("webhook_mode"),
            "storage_ready": bool(snapshot.get("storage_ready")),
            "storage_backend": snapshot.get("storage_backend"),
            "storage_message": snapshot.get("storage_message"),
            "runtime_attached": True,
            "public_routes_ready": True,
            "refresh_available": bool(snapshot.get("refresh_available")),
            "refresh_cooldown_seconds": snapshot.get("refresh_cooldown_seconds"),
            "timing_source": snapshot.get("timing_source"),
            "reminder_ready": bool(snapshot.get("reminder_ready")),
            "reminder_delivery_mode": snapshot.get("reminder_delivery_mode"),
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


def _http_status_text(status_code: int) -> str:
    return {
        200: "200 OK",
        400: "400 Bad Request",
        404: "404 Not Found",
        405: "405 Method Not Allowed",
        408: "408 Request Timeout",
        413: "413 Payload Too Large",
        500: "500 Internal Server Error",
        503: "503 Service Unavailable",
    }.get(status_code, f"{status_code} Unknown")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class _BodyTooLarge(Exception):
    pass


class _BadRequest(Exception):
    pass


class _RequestTimeout(Exception):
    pass
