from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from bdayblaze.bot import BdayblazeBot


class FakeLogger:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, dict[str, object]]] = []
        self.exception_calls: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **fields: object) -> None:
        self.warning_calls.append((event, fields))

    def exception(self, event: str, **fields: object) -> None:
        self.exception_calls.append((event, fields))


class FakeResponse:
    def __init__(self, *, done: bool = False) -> None:
        self._done = done
        self.messages: list[str] = []

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, message: str, *, ephemeral: bool) -> None:
        assert ephemeral is True
        self.messages.append(message)


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str, *, ephemeral: bool) -> None:
        assert ephemeral is True
        self.messages.append(message)


def _interaction(*, manage_guild: bool, done: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name="birthdayadmin studio"),
        guild_id=123,
        user=SimpleNamespace(
            id=456,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
        ),
        response=FakeResponse(done=done),
        followup=FakeFollowup(),
    )


def _bot() -> BdayblazeBot:
    bot = object.__new__(BdayblazeBot)
    bot._logger = FakeLogger()
    return bot


@pytest.mark.asyncio
async def test_app_command_error_surfaces_safe_http_payload_hint_for_admins() -> None:
    interaction = _interaction(manage_guild=True)
    bot = _bot()
    response = SimpleNamespace(status=400, reason="Bad Request")
    error = discord.HTTPException(
        response,  # type: ignore[arg-type]
        {"code": 50035, "message": "Invalid Form Body"},
    )

    await bot.on_app_command_error(interaction, error)  # type: ignore[arg-type]

    assert interaction.response.messages
    assert "admin panel or preview payload" in interaction.response.messages[0]
    assert "Action:" in interaction.response.messages[0]
    assert "BDAY-UI-400" in interaction.response.messages[0]
    assert bot._logger.warning_calls


@pytest.mark.asyncio
async def test_app_command_error_hides_internal_hint_for_non_admins() -> None:
    interaction = _interaction(manage_guild=False)
    bot = _bot()

    await bot.on_app_command_error(interaction, RuntimeError("boom"))  # type: ignore[arg-type]

    assert interaction.response.messages == [
        "Something went wrong while handling that command."
    ]
    assert bot._logger.exception_calls
