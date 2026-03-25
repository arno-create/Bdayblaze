from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from bdayblaze.bot import BdayblazeBot
from bdayblaze.config import Settings
from bdayblaze.container import ServiceContainer
from bdayblaze.db.migrations import apply_migrations
from bdayblaze.db.pool import create_pool
from bdayblaze.discord.gateway import DiscordSchedulerGateway
from bdayblaze.discord.studio_audit import StudioAuditLogger
from bdayblaze.domain.models import RuntimeStatus, SchedulerMetrics
from bdayblaze.http_server import HttpHealthServer
from bdayblaze.logging import configure_logging, get_logger
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.experience_service import ExperienceService
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    BirthdaySchedulerRunner,
    BirthdaySchedulerService,
    DirectSendResult,
)
from bdayblaze.services.settings_service import SettingsService


async def _build_container(settings: Settings, runtime_status: RuntimeStatus) -> ServiceContainer:
    logger = get_logger(component="startup")
    runtime_status.startup_phase = "database_connect"
    pool = await create_pool(settings.database_url)
    try:
        runtime_status.db_pool_ready_at_utc = datetime.now(UTC)
        runtime_status.startup_phase = "database_ready"
        logger.info("database_pool_ready")
        if settings.auto_run_migrations:
            runtime_status.migrations_started_at_utc = datetime.now(UTC)
            runtime_status.startup_phase = "migrations_running"
            logger.info("migrations_started")
            try:
                applied = await apply_migrations(pool)
            except Exception:
                runtime_status.migrations_failed_at_utc = datetime.now(UTC)
                runtime_status.startup_phase = "migrations_failed"
                logger.exception("migrations_failed")
                raise
            runtime_status.migrations_completed_at_utc = datetime.now(UTC)
            runtime_status.startup_phase = "migrations_completed"
            logger.info("migrations_completed", applied=applied)
        repository = PostgresRepository(pool)
        birthday_service = BirthdayService(repository)
        experience_service = ExperienceService(repository)
        settings_service = SettingsService(repository)
        studio_audit_logger = StudioAuditLogger(settings_service)
        metrics = SchedulerMetrics()
        placeholder_gateway = _DeferredGateway()
        scheduler_service = BirthdaySchedulerService(
            repository,
            placeholder_gateway,
            metrics,
            batch_size=settings.scheduler_batch_size,
            recovery_grace_hours=settings.recovery_grace_hours,
            scheduler_max_sleep_seconds=settings.scheduler_max_sleep_seconds,
        )
        health_service = HealthService(
            repository,
            metrics,
            recovery_grace_hours=settings.recovery_grace_hours,
            scheduler_max_sleep_seconds=settings.scheduler_max_sleep_seconds,
        )
        return ServiceContainer(
            settings=settings,
            pool=pool,
            repository=repository,
            birthday_service=birthday_service,
            experience_service=experience_service,
            settings_service=settings_service,
            health_service=health_service,
            studio_audit_logger=studio_audit_logger,
            scheduler_metrics=metrics,
            runtime_status=runtime_status,
            scheduler_service=scheduler_service,
            scheduler_runner=BirthdaySchedulerRunner(scheduler_service, runtime_status),
        )
    except Exception:
        await pool.close()
        raise


async def _run_bot(settings: Settings) -> None:
    logger = get_logger(component="startup")
    runtime_status = RuntimeStatus(process_started_at_utc=datetime.now(UTC))
    logger.info("startup_begin")
    container = await _build_container(settings, runtime_status)
    bot = BdayblazeBot(container)
    container.scheduler_service.attach_gateway(DiscordSchedulerGateway(bot))
    http_server = (
        HttpHealthServer(
            metrics=container.scheduler_metrics,
            runtime_status=container.runtime_status,
            host=settings.bind_host,
            port=settings.bind_port,
            scheduler_max_sleep_seconds=settings.scheduler_max_sleep_seconds,
        )
        if settings.bind_port is not None
        else None
    )
    try:
        if http_server is not None:
            container.runtime_status.startup_phase = "health_server_starting"
            logger.info(
                "http_health_server_starting",
                host=settings.bind_host,
                port=settings.bind_port,
            )
            try:
                await http_server.start()
            except Exception:
                container.runtime_status.health_server_failed_at_utc = datetime.now(UTC)
                container.runtime_status.startup_phase = "health_server_failed"
                logger.exception(
                    "http_health_server_failed",
                    host=settings.bind_host,
                    port=settings.bind_port,
                )
                raise
        container.runtime_status.bot_login_started_at_utc = datetime.now(UTC)
        container.runtime_status.startup_phase = "bot_login"
        logger.info("bot_login_started")
        await bot.start(settings.discord_token)
    except Exception:
        container.runtime_status.unexpected_shutdown_at_utc = datetime.now(UTC)
        container.runtime_status.startup_phase = "shutdown_error"
        logger.exception("bot_run_failed")
        raise
    finally:
        if http_server is not None:
            await http_server.stop()
        if container.runtime_status.unexpected_shutdown_at_utc is None:
            logger.info("bot_stopped")
        logger.info("shutdown_complete")


async def _run_migrations(settings: Settings) -> None:
    logger = get_logger(component="migrations")
    pool = await create_pool(settings.database_url)
    try:
        logger.info("migrations_started")
        try:
            applied = await apply_migrations(pool)
        except Exception:
            logger.exception("migrations_failed")
            raise
        logger.info("migrations_applied", applied=applied)
    finally:
        await pool.close()


def main() -> None:
    load_dotenv()
    if sys.version_info >= (3, 14):
        raise RuntimeError(
            "Python 3.14 is not supported by this deployment yet. Use Python 3.12 or 3.13."
        )
    parser = argparse.ArgumentParser(prog="bdayblaze")
    parser.add_argument("command", choices=["run", "migrate"], nargs="?", default="run")
    args = parser.parse_args()

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if args.command == "migrate":
        asyncio.run(_run_migrations(settings))
        return
    asyncio.run(_run_bot(settings))


class _DeferredGateway:
    async def find_announcement_message(self, **_: object) -> int | None:
        raise RuntimeError("Gateway not attached yet.")

    async def send_birthday_announcement(self, **_: object) -> AnnouncementSendResult:
        raise RuntimeError("Gateway not attached yet.")

    async def send_anniversary_announcement(self, **_: object) -> AnnouncementSendResult:
        raise RuntimeError("Gateway not attached yet.")

    async def send_birthday_dm(self, **_: object) -> DirectSendResult:
        raise RuntimeError("Gateway not attached yet.")

    async def send_recurring_announcement(self, **_: object) -> DirectSendResult:
        raise RuntimeError("Gateway not attached yet.")

    async def send_capsule_reveal(self, **_: object) -> DirectSendResult:
        raise RuntimeError("Gateway not attached yet.")

    async def add_birthday_role(self, **_: object) -> str:
        raise RuntimeError("Gateway not attached yet.")

    async def remove_birthday_role(self, **_: object) -> str:
        raise RuntimeError("Gateway not attached yet.")


if __name__ == "__main__":
    main()
