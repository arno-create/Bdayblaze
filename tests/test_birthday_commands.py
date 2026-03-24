from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from bdayblaze.discord.cogs.birthday import BirthdayGroup, _remove_active_birthday_role_if_needed
from bdayblaze.domain.models import AnnouncementDeliveryReadiness, GuildSettings


class DummyBirthdayService:
    pass


class DummyHealthService:
    pass


class FakeSettingsService:
    def __init__(self, settings: GuildSettings, readiness: AnnouncementDeliveryReadiness) -> None:
        self.settings = settings
        self.readiness = readiness

    async def get_settings(self, guild_id: int) -> GuildSettings:
        assert guild_id == self.settings.guild_id
        return self.settings

    async def describe_announcement_delivery(
        self,
        guild: object,
    ) -> AnnouncementDeliveryReadiness:
        assert getattr(guild, "id", None) == self.settings.guild_id
        return self.readiness


class FakeResponse:
    async def defer(self, *, ephemeral: bool) -> None:
        assert ephemeral is True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.messages.append({"args": args, "kwargs": kwargs})


class FakeUser:
    def __init__(self, *, dm_forbidden: bool) -> None:
        self.dm_forbidden = dm_forbidden
        self.sent: list[dict[str, object]] = []

    async def send(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if self.dm_forbidden:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                "DMs closed",
            )
        self.sent.append({"args": args, "kwargs": kwargs})


class FakeInteraction:
    def __init__(self, guild: object, user: FakeUser) -> None:
        self.guild = guild
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, role: FakeRole) -> None:
        self.id = user_id
        self.roles = [role]
        self.removed_reasons: list[str] = []

    async def remove_roles(self, role: FakeRole, *, reason: str) -> None:
        assert role in self.roles
        self.removed_reasons.append(reason)
        self.roles.remove(role)


class FakeGuild:
    def __init__(
        self,
        guild_id: int,
        role: FakeRole | None = None,
        member: FakeMember | None = None,
    ) -> None:
        self.id = guild_id
        self.name = "Birthday Club"
        self._role = role
        self._member = member

    def get_role(self, role_id: int) -> FakeRole | None:
        if self._role and self._role.id == role_id:
            return self._role
        return None

    def get_member(self, user_id: int) -> FakeMember | None:
        if self._member and self._member.id == user_id:
            return self._member
        return None

    async def fetch_member(self, user_id: int) -> FakeMember:
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")


@pytest.mark.asyncio
async def test_test_message_falls_back_to_ephemeral_when_dms_are_closed() -> None:
    settings = GuildSettings(
        guild_id=1,
        announcement_channel_id=None,
        default_timezone="UTC",
        birthday_role_id=None,
        announcements_enabled=False,
        role_enabled=False,
        celebration_mode="quiet",
        announcement_theme="classic",
        announcement_template=None,
    )
    service = FakeSettingsService(
        settings,
        AnnouncementDeliveryReadiness(
            status="blocked",
            summary="Preview ready. Live delivery is disabled in this server.",
            details=("Announcements are currently disabled.",),
        ),
    )
    group = BirthdayGroup(  # type: ignore[arg-type]
        birthday_service=DummyBirthdayService(),  # type: ignore[arg-type]
        settings_service=service,  # type: ignore[arg-type]
        health_service=DummyHealthService(),  # type: ignore[arg-type]
    )
    interaction = FakeInteraction(FakeGuild(1), FakeUser(dm_forbidden=True))

    await group.test_message(interaction)  # type: ignore[arg-type]

    assert interaction.followup.messages
    followup = interaction.followup.messages[0]["kwargs"]
    assert followup["ephemeral"] is True
    assert len(followup["embeds"]) == 3


@pytest.mark.asyncio
async def test_remove_active_birthday_role_if_needed_removes_saved_role() -> None:
    role = FakeRole(55)
    member = FakeMember(42, role)
    guild = FakeGuild(1, role=role, member=member)

    await _remove_active_birthday_role_if_needed(
        guild, 42, 55, reason="Birthday cleanup"
    )  # type: ignore[arg-type]

    assert member.removed_reasons == ["Birthday cleanup"]
