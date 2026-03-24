from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from bdayblaze.bot import BdayblazeBot
from bdayblaze.config import Settings
from bdayblaze.container import ServiceContainer
from bdayblaze.db.migrations import apply_migrations
from bdayblaze.db.pool import create_pool
from bdayblaze.discord.gateway import DiscordSchedulerGateway
from bdayblaze.domain.models import SchedulerMetrics
from bdayblaze.logging import configure_logging, get_logger
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.scheduler import (
    AnnouncementSendResult,
    BirthdaySchedulerRunner,
    BirthdaySchedulerService,
)
from bdayblaze.services.settings_service import SettingsService


async def _build_container(settings: Settings) -> ServiceContainer:
    pool = await create_pool(settings.database_url)
    if settings.auto_run_migrations:
        await apply_migrations(pool)
    repository = PostgresRepository(pool)
    birthday_service = BirthdayService(repository)
    settings_service = SettingsService(repository)
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
        settings_service=settings_service,
        health_service=health_service,
        scheduler_service=scheduler_service,
        scheduler_runner=BirthdaySchedulerRunner(scheduler_service),
    )


async def _run_bot(settings: Settings) -> None:
    container = await _build_container(settings)
    bot = BdayblazeBot(container)
    container.scheduler_service.attach_gateway(DiscordSchedulerGateway(bot))
    await bot.start(settings.discord_token)


async def _run_migrations(settings: Settings) -> None:
    pool = await create_pool(settings.database_url)
    try:
        applied = await apply_migrations(pool)
    finally:
        await pool.close()
    get_logger(component="migrations").info("migrations_applied", applied=applied)


def main() -> None:
    load_dotenv()
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

    async def add_birthday_role(self, **_: object) -> str:
        raise RuntimeError("Gateway not attached yet.")

    async def remove_birthday_role(self, **_: object) -> str:
        raise RuntimeError("Gateway not attached yet.")


if __name__ == "__main__":
    main()
