from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import discord
import pytest

from bdayblaze.discord.cogs.info import InfoCog
from bdayblaze.discord.ui import info


class FakeResponse:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_message(self, *args: object, **kwargs: object) -> None:
        assert not args
        self.calls.append(dict(kwargs))


def _interaction() -> SimpleNamespace:
    return SimpleNamespace(response=FakeResponse())


def _button_snapshot(view: object) -> list[tuple[str | None, str | None, discord.ButtonStyle]]:
    assert isinstance(view, discord.ui.View)
    return [
        (child.label, child.url, child.style)
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_name", "expected_embed"),
    [
        ("help", info.build_help_embed),
        ("about", info.build_about_embed),
        ("support", info.build_support_embed),
    ],
)
async def test_info_commands_send_ephemeral_embed_with_shared_links_view(
    command_name: str,
    expected_embed: Callable[[], discord.Embed],
) -> None:
    interaction = _interaction()
    cog = InfoCog()

    await getattr(InfoCog, command_name).callback(cog, interaction)  # type: ignore[misc]

    assert len(interaction.response.calls) == 1
    payload = interaction.response.calls[0]

    assert payload["ephemeral"] is True
    assert isinstance(payload["embed"], discord.Embed)
    assert payload["embed"].to_dict() == expected_embed().to_dict()
    assert _button_snapshot(payload["view"]) == _button_snapshot(info.build_info_links_view())
