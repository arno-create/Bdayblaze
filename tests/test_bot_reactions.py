from __future__ import annotations

from types import SimpleNamespace

import pytest

from bdayblaze.bot import BdayblazeBot


class FakeLogger:
    def warning(self, event: str, **fields: object) -> None:
        return None

    def exception(self, event: str, **fields: object) -> None:
        return None


class FakeExperienceService:
    def __init__(self) -> None:
        self.tracked = True
        self.channel_id = 123
        self.refresh_calls: list[tuple[int, int, int]] = []
        self.disable_calls: list[tuple[int, int]] = []

    async def has_tracked_birthday_announcement_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> bool:
        return self.tracked

    async def fetch_announcement_channel_for_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> int | None:
        return self.channel_id

    async def refresh_birthday_announcement_reactions(
        self,
        guild_id: int,
        message_id: int,
        reaction_count: int,
    ) -> list[object]:
        self.refresh_calls.append((guild_id, message_id, reaction_count))
        return []

    async def disable_birthday_announcement_reaction_tracking(
        self,
        guild_id: int,
        message_id: int,
    ) -> list[object]:
        self.disable_calls.append((guild_id, message_id))
        return []


class FakeChannel:
    def __init__(self, counts: list[int]) -> None:
        self._counts = counts

    async def fetch_message(self, message_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            reactions=[SimpleNamespace(count=count) for count in self._counts]
        )


def _bot(channel: FakeChannel, experience_service: FakeExperienceService) -> BdayblazeBot:
    bot = object.__new__(BdayblazeBot)
    bot.container = SimpleNamespace(experience_service=experience_service)
    bot._logger = FakeLogger()
    bot._reaction_refresh_tasks = {}
    bot._reaction_message_cache = {}
    bot._reaction_channel_cache = {}
    bot.get_channel = lambda channel_id: channel  # type: ignore[assignment]
    return bot


@pytest.mark.asyncio
async def test_refresh_birthday_reactions_for_message_updates_total_reaction_count() -> None:
    experience_service = FakeExperienceService()
    bot = _bot(FakeChannel([2, 3, 1]), experience_service)

    refreshed = await bot.refresh_birthday_reactions_for_message(
        guild_id=1,
        message_id=777,
    )

    assert refreshed is True
    assert experience_service.refresh_calls == [(1, 777, 6)]


@pytest.mark.asyncio
async def test_refresh_birthday_reactions_for_message_short_circuits_for_untracked_messages(
) -> None:
    experience_service = FakeExperienceService()
    experience_service.tracked = False
    bot = _bot(FakeChannel([4]), experience_service)

    refreshed = await bot.refresh_birthday_reactions_for_message(
        guild_id=1,
        message_id=888,
    )

    assert refreshed is False
    assert experience_service.refresh_calls == []
