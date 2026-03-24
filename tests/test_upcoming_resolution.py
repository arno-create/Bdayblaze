from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from bdayblaze.discord.member_resolution import resolve_guild_members


class FakeMember:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeGuild:
    def __init__(self) -> None:
        self.cached = {1: FakeMember(1)}
        self.fetched = {2: FakeMember(2)}

    def get_member(self, user_id: int) -> FakeMember | None:
        return self.cached.get(user_id)

    async def fetch_member(self, user_id: int) -> FakeMember:
        if user_id not in self.fetched:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                "missing",
            )
        return self.fetched[user_id]


@pytest.mark.asyncio
async def test_resolve_guild_members_uses_cache_then_fetch_and_skips_missing() -> None:
    guild = FakeGuild()

    resolved = await resolve_guild_members(guild, [1, 2, 3])  # type: ignore[arg-type]

    assert [member.id for _, member in resolved] == [1, 2]
