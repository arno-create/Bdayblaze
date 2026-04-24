from __future__ import annotations

import pytest

from bdayblaze.bot import BdayblazeBot
from bdayblaze.discord.cogs.birthday import BirthdayAdminGroup, BirthdayGroup
from bdayblaze.discord.cogs.info import InfoCog


class FakeLogger:
    def info(self, event: str, **fields: object) -> None:
        self.event = event
        self.fields = fields


def _bot(
    *guild_sync_ids: int,
) -> tuple[BdayblazeBot, list[object], list[object | None], list[object]]:
    from types import SimpleNamespace

    bot = BdayblazeBot(
        SimpleNamespace(
            birthday_service=object(),
            experience_service=object(),
            settings_service=object(),
            health_service=object(),
            studio_audit_logger=object(),
            vote_service=object(),
            settings=SimpleNamespace(guild_sync_ids=list(guild_sync_ids)),
        )
    )
    bot._logger = FakeLogger()
    added_cogs: list[object] = []
    sync_calls: list[object | None] = []
    error_handlers: list[object] = []

    async def add_cog(cog: object) -> None:
        added_cogs.append(cog)

    def error(handler: object) -> object:
        error_handlers.append(handler)
        return handler

    async def sync(guild: object | None = None) -> list[object]:
        sync_calls.append(guild)
        return []

    bot.add_cog = add_cog  # type: ignore[method-assign]
    bot.tree.error = error  # type: ignore[method-assign]
    bot.tree.sync = sync  # type: ignore[method-assign]
    return bot, added_cogs, sync_calls, error_handlers


@pytest.mark.asyncio
async def test_setup_hook_registers_public_and_admin_cogs_before_global_sync() -> None:
    bot, added_cogs, sync_calls, error_handlers = _bot()

    await bot.setup_hook()

    assert [type(cog).__name__ for cog in added_cogs] == [
        "BirthdayGroup",
        "BirthdayAdminGroup",
        "InfoCog",
        "VoteCog",
    ]
    assert len(error_handlers) == 1
    assert error_handlers[0].__self__ is bot
    assert error_handlers[0].__func__ is BdayblazeBot.on_app_command_error
    assert sync_calls == [None]


@pytest.mark.asyncio
async def test_setup_hook_preserves_guild_scoped_sync_behavior() -> None:
    bot, added_cogs, sync_calls, _ = _bot(101, 202)

    await bot.setup_hook()

    assert [type(cog).__name__ for cog in added_cogs] == [
        "BirthdayGroup",
        "BirthdayAdminGroup",
        "InfoCog",
        "VoteCog",
    ]
    assert [guild.id for guild in sync_calls] == [101, 202]
