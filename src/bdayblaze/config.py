from __future__ import annotations

from dataclasses import dataclass
from os import getenv


def _parse_bool(name: str, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(name: str, default: int) -> int:
    value = getenv(name)
    if value is None:
        return default
    return int(value)


def _parse_int_list(name: str) -> tuple[int, ...]:
    raw = getenv(name, "").strip()
    if not raw:
        return ()
    return tuple(int(part.strip()) for part in raw.split(",") if part.strip())


@dataclass(slots=True, frozen=True)
class Settings:
    discord_token: str
    database_url: str
    log_level: str
    auto_run_migrations: bool
    recovery_grace_hours: int
    scheduler_max_sleep_seconds: int
    scheduler_batch_size: int
    guild_sync_ids: tuple[int, ...]
    bind_host: str
    bind_port: int | None

    @classmethod
    def from_env(cls) -> Settings:
        token = getenv("DISCORD_TOKEN", "").strip()
        database_url = getenv("DATABASE_URL", "").strip()
        if not token:
            raise RuntimeError("DISCORD_TOKEN is required.")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required.")
        return cls(
            discord_token=token,
            database_url=database_url,
            log_level=getenv("BDAYBLAZE_LOG_LEVEL", "INFO").strip().upper(),
            auto_run_migrations=_parse_bool("BDAYBLAZE_AUTO_RUN_MIGRATIONS", False),
            recovery_grace_hours=_parse_int("BDAYBLAZE_RECOVERY_GRACE_HOURS", 36),
            scheduler_max_sleep_seconds=_parse_int("BDAYBLAZE_SCHEDULER_MAX_SLEEP_SECONDS", 300),
            scheduler_batch_size=_parse_int("BDAYBLAZE_SCHEDULER_BATCH_SIZE", 25),
            guild_sync_ids=_parse_int_list("BDAYBLAZE_GUILD_SYNC_IDS"),
            bind_host=getenv("BDAYBLAZE_BIND_HOST", "0.0.0.0").strip() or "0.0.0.0",
            bind_port=int(getenv("PORT")) if getenv("PORT") else None,
        )
