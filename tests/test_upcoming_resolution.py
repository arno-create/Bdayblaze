from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import discord
import pytest

from bdayblaze.discord.cogs.birthday import _resolve_upcoming_members
from bdayblaze.domain.models import BirthdayPreview


class FakeMember:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.mention = f"<@{user_id}>"


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


def _preview(user_id: int) -> BirthdayPreview:
    return BirthdayPreview(
        user_id=user_id,
        birth_month=3,
        birth_day=24,
        next_occurrence_at_utc=datetime(2026, 3, 24, tzinfo=UTC),
        effective_timezone="UTC",
    )


@pytest.mark.asyncio
async def test_resolve_upcoming_members_uses_cache_then_fetch_and_skips_missing() -> None:
    guild = FakeGuild()

    resolved = await _resolve_upcoming_members(guild, [_preview(1), _preview(2), _preview(3)])  # type: ignore[arg-type]

    assert [member.id for _, member in resolved] == [1, 2]
