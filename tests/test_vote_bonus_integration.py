from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import discord
import pytest

from bdayblaze.discord.cogs.birthday import BirthdayGroup


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, *, ephemeral: bool) -> None:
        assert ephemeral is True
        self.deferred = True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, *args: object, **kwargs: object) -> None:
        payload = dict(kwargs)
        if args:
            payload["content"] = args[0]
        self.messages.append(payload)


class FakeExperienceService:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []

    async def add_wish(self, **kwargs: object) -> SimpleNamespace:
        self.add_calls.append(dict(kwargs))
        return SimpleNamespace(
            wish_text=kwargs["wish_text"],
            link_url=kwargs["link_url"],
        )


class FakeVoteService:
    def __init__(self, *, wish_character_limit: int) -> None:
        self.wish_character_limit = wish_character_limit

    async def get_vote_bonus_status(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            lane_state="inactive" if self.wish_character_limit == 350 else "active_exact",
            active=self.wish_character_limit > 350,
            wish_character_limit=self.wish_character_limit,
            timeline_entry_limit=12 if self.wish_character_limit > 350 else 6,
        )


def _interaction(*, vote_service: FakeVoteService) -> SimpleNamespace:
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=42),
        client=SimpleNamespace(container=SimpleNamespace(vote_service=vote_service)),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )


@pytest.mark.asyncio
async def test_wish_add_points_users_to_vote_when_bonus_limit_is_needed() -> None:
    experience_service = FakeExperienceService()
    cog = BirthdayGroup(
        object(),
        experience_service,  # type: ignore[arg-type]
        object(),
        object(),
        object(),
    )
    interaction = _interaction(vote_service=FakeVoteService(wish_character_limit=350))

    await BirthdayGroup.wish_add.callback(  # type: ignore[misc]
        cog,
        interaction,
        SimpleNamespace(id=99, mention="@Jamie"),
        "x" * 400,
        None,
    )

    assert experience_service.add_calls == []
    assert interaction.followup.messages
    assert "/vote" in str(interaction.followup.messages[0]["content"])


@pytest.mark.asyncio
async def test_wish_add_allows_longer_message_when_vote_bonus_is_active() -> None:
    experience_service = FakeExperienceService()
    cog = BirthdayGroup(
        object(),
        experience_service,  # type: ignore[arg-type]
        object(),
        object(),
        object(),
    )
    interaction = _interaction(vote_service=FakeVoteService(wish_character_limit=500))

    await BirthdayGroup.wish_add.callback(  # type: ignore[misc]
        cog,
        interaction,
        SimpleNamespace(id=99, mention="@Jamie"),
        "x" * 400,
        None,
    )

    assert len(experience_service.add_calls) == 1
    assert experience_service.add_calls[0]["wish_text"] == "x" * 400
    assert any(
        isinstance(message.get("embed"), discord.Embed)
        for message in interaction.followup.messages
    )
