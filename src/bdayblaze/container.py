from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from bdayblaze.config import Settings
from bdayblaze.domain.models import RuntimeStatus, SchedulerMetrics
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.discord.studio_audit import StudioAuditLogger
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.health_service import HealthService
from bdayblaze.services.scheduler import BirthdaySchedulerRunner, BirthdaySchedulerService
from bdayblaze.services.settings_service import SettingsService


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    pool: asyncpg.Pool
    repository: PostgresRepository
    birthday_service: BirthdayService
    settings_service: SettingsService
    health_service: HealthService
    studio_audit_logger: StudioAuditLogger
    scheduler_metrics: SchedulerMetrics
    runtime_status: RuntimeStatus
    scheduler_service: BirthdaySchedulerService
    scheduler_runner: BirthdaySchedulerRunner
