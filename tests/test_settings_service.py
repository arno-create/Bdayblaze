from __future__ import annotations

from types import SimpleNamespace

import pytest

from bdayblaze.domain.models import GuildSettings
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.settings_service import SettingsService


class FakeSettingsRepository:
    def __init__(self, settings: GuildSettings | None = None) -> None:
        self.settings = settings
        self.saved: GuildSettings | None = None

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        assert self.settings is None or self.settings.guild_id == guild_id
        return self.settings

    async def upsert_guild_settings(self, settings: GuildSettings) -> GuildSettings:
        self.saved = settings
        self.settings = settings
        return settings


class FakeRole:
    def __init__(self, *, position: int) -> None:
        self.position = position

    def __le__(self, other: object) -> bool:
        if not isinstance(other, FakeRole):
            return NotImplemented
        return self.position <= other.position


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=FakeRole(position=10),
        )

    def get_channel(self, channel_id: int) -> None:
        return None

    def get_role(self, role_id: int) -> None:
        return None


@pytest.mark.asyncio
async def test_settings_service_normalizes_saved_announcement_template() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_settings(
        FakeGuild(1),  # type: ignore[arg-type]
        announcement_template="  Hello {birthday.names}!  ",
    )

    assert saved.announcement_template == "Hello {birthday.names}!"
    assert repository.saved is not None
    assert repository.saved.announcement_template == "Hello {birthday.names}!"


@pytest.mark.asyncio
async def test_settings_service_rejects_unknown_template_placeholders() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match=r"\{user.secret\}"):
        await service.update_settings(
            FakeGuild(1),  # type: ignore[arg-type]
            announcement_template="Hello {user.secret}",
        )


@pytest.mark.asyncio
async def test_settings_service_resets_blank_template_to_default_storage() -> None:
    settings = GuildSettings.default(1)
    repository = FakeSettingsRepository(settings)
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_settings(
        FakeGuild(1),  # type: ignore[arg-type]
        announcement_template="   ",
    )

    assert saved.announcement_template is None


@pytest.mark.asyncio
async def test_settings_service_saves_announcement_theme() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_settings(
        FakeGuild(1),  # type: ignore[arg-type]
        announcement_theme="cute",
    )

    assert saved.announcement_theme == "cute"


@pytest.mark.asyncio
async def test_describe_announcement_delivery_reports_disabled_announcements() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    readiness = await service.describe_announcement_delivery(FakeGuild(1))  # type: ignore[arg-type]

    assert readiness.status == "blocked"
    assert "disabled" in readiness.summary
