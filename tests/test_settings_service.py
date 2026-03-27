from __future__ import annotations

from types import SimpleNamespace

import pytest

from bdayblaze.domain.media_validation import mark_validated_direct_media_url
from bdayblaze.domain.models import (
    AnnouncementSurfaceSettings,
    GuildSettings,
)
from bdayblaze.services.errors import ValidationError
from bdayblaze.services.settings_service import SettingsService


class FakeSettingsRepository:
    def __init__(
        self,
        settings: GuildSettings | None = None,
        *,
        surfaces: dict[str, AnnouncementSurfaceSettings] | None = None,
    ) -> None:
        self.settings = settings
        self.surfaces = surfaces or {}
        self.saved: GuildSettings | None = None
        self.saved_surface: AnnouncementSurfaceSettings | None = None
        self.timezone_refresh_calls: list[tuple[int, str]] = []

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        assert self.settings is None or self.settings.guild_id == guild_id
        return self.settings

    async def list_guild_announcement_surfaces(
        self,
        guild_id: int,
    ) -> dict[str, AnnouncementSurfaceSettings]:
        return {
            kind: surface
            for kind, surface in self.surfaces.items()
            if surface.guild_id == guild_id
        }

    async def upsert_guild_settings(self, settings: GuildSettings) -> GuildSettings:
        self.saved = settings
        self.settings = settings
        return settings

    async def upsert_guild_announcement_surface(
        self,
        surface: AnnouncementSurfaceSettings,
    ) -> AnnouncementSurfaceSettings:
        self.saved_surface = surface
        self.surfaces[surface.surface_kind] = surface
        return surface

    async def delete_guild_announcement_surface(self, guild_id: int, surface_kind: str) -> None:
        self.surfaces.pop(surface_kind, None)

    async def refresh_timezone_bound_schedules(
        self,
        guild_id: int,
        *,
        default_timezone: str,
        now_utc: object,
    ) -> None:
        self.timezone_refresh_calls.append((guild_id, default_timezone))


class FakeRole:
    def __init__(self, *, position: int = 1, managed: bool = False, default: bool = False) -> None:
        self.position = position
        self.managed = managed
        self._default = default

    def __le__(self, other: object) -> bool:
        if not isinstance(other, FakeRole):
            return NotImplemented
        return self.position <= other.position

    def is_default(self) -> bool:
        return self._default


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
async def test_settings_service_rejects_server_anniversary_placeholder_on_member_anniversary(
) -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match=r"Use \{anniversary\.years\} instead"):
        await service.update_settings(
            FakeGuild(1),  # type: ignore[arg-type]
            anniversary_template="Happy {server_anniversary.years_since_creation}",
        )


@pytest.mark.asyncio
async def test_settings_service_resets_blank_template_to_default_storage() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
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
async def test_settings_service_saves_global_celebration_behavior() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_settings(
        FakeGuild(1),  # type: ignore[arg-type]
        celebration_mode="party",
    )

    assert saved.celebration_mode == "party"


@pytest.mark.asyncio
async def test_settings_service_rejects_invalid_studio_media_url() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="must use HTTPS"):
        await service.update_announcement_surface(
            FakeGuild(1),  # type: ignore[arg-type]
            surface_kind="birthday_announcement",
            image_url="http://example.com/banner.png",
        )


@pytest.mark.asyncio
async def test_settings_service_accepts_signed_extensionless_media_url() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="Media Tools first"):
        await service.update_announcement_surface(
            FakeGuild(1),  # type: ignore[arg-type]
            surface_kind="birthday_announcement",
            image_url="https://cdn.example.com/assets/banner?sig=abc123",
        )


@pytest.mark.asyncio
async def test_settings_service_accepts_validated_extensionless_media_url() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]
    validated_url = mark_validated_direct_media_url(
        "https://cdn.example.com/assets/banner?sig=abc123"
    )

    saved = await service.update_validated_media(
        FakeGuild(1),  # type: ignore[arg-type]
        surface_kind="birthday_announcement",
        announcement_image_url=validated_url,
    )

    assert saved.image_url == validated_url


@pytest.mark.asyncio
async def test_settings_service_rejects_unvalidated_extensionless_media_url() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="Media Tools first"):
        await service.update_validated_media(
            FakeGuild(1),  # type: ignore[arg-type]
            surface_kind="birthday_announcement",
            announcement_image_url="https://cdn.example.com/assets/banner?sig=abc123",
        )


@pytest.mark.asyncio
async def test_settings_service_keeps_last_saved_media_when_new_value_is_rejected() -> None:
    repository = FakeSettingsRepository(
        GuildSettings.default(1),
        surfaces={
            "birthday_announcement": AnnouncementSurfaceSettings(
                guild_id=1,
                surface_kind="birthday_announcement",
                image_url=mark_validated_direct_media_url(
                    "https://cdn.example.com/assets/banner?sig=abc123"
                ),
            )
        },
    )
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="unsupported \\.zip content"):
        await service.update_validated_media(
            FakeGuild(1),  # type: ignore[arg-type]
            surface_kind="birthday_announcement",
            announcement_image_url="https://cdn.example.com/assets/banner.zip",
        )

    assert (
        repository.surfaces["birthday_announcement"].image_url
        == mark_validated_direct_media_url("https://cdn.example.com/assets/banner?sig=abc123")
    )


@pytest.mark.asyncio
async def test_settings_service_preserves_validated_media_on_unrelated_update() -> None:
    repository = FakeSettingsRepository(
        GuildSettings.default(1),
        surfaces={
            "birthday_announcement": AnnouncementSurfaceSettings(
                guild_id=1,
                surface_kind="birthday_announcement",
                image_url=mark_validated_direct_media_url(
                    "https://cdn.example.com/assets/banner?sig=abc123"
                ),
            )
        },
    )
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_settings(
        FakeGuild(1),  # type: ignore[arg-type]
        announcement_theme="cute",
    )

    assert saved.announcement_theme == "cute"
    assert repository.surfaces["birthday_announcement"].image_url is not None


@pytest.mark.asyncio
async def test_settings_service_rejects_obvious_non_image_media_url() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="unsupported \\.zip content"):
        await service.update_announcement_surface(
            FakeGuild(1),  # type: ignore[arg-type]
            surface_kind="birthday_announcement",
            thumbnail_url="https://example.com/file.zip",
        )


@pytest.mark.asyncio
async def test_settings_service_rejects_invalid_studio_accent_color() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="6-digit hex"):
        await service.update_settings(
            FakeGuild(1),  # type: ignore[arg-type]
            announcement_accent_color="#GGGGGG",
        )


@pytest.mark.asyncio
async def test_settings_service_blocks_profane_announcement_template() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="blocked profane or vulgar language"):
        await service.update_settings(
            FakeGuild(1),  # type: ignore[arg-type]
            announcement_template="Happy birthday you bitch",
        )


@pytest.mark.asyncio
async def test_settings_service_blocks_unsafe_media_url_keyword() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="blocked unsafe keywords"):
        await service.update_validated_media(
            FakeGuild(1),  # type: ignore[arg-type]
            surface_kind="birthday_announcement",
            announcement_thumbnail_url="https://cdn.example.com/nsfw/banner.png",
        )


@pytest.mark.asyncio
async def test_describe_announcement_delivery_reports_disabled_announcements() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    readiness = await service.describe_announcement_delivery(FakeGuild(1))  # type: ignore[arg-type]

    assert readiness.status == "blocked"
    assert "disabled" in readiness.summary


@pytest.mark.asyncio
async def test_describe_delivery_reports_birthday_dm_disabled_until_enabled() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]
    guild = FakeGuild(1)

    blocked = await service.describe_delivery(guild, kind="birthday_dm")
    await service.update_settings(guild, birthday_dm_enabled=True)
    ready = await service.describe_delivery(guild, kind="birthday_dm")

    assert blocked.status == "blocked"
    assert "disabled" in blocked.summary
    assert ready.status == "ready"
    assert "best-effort" in ready.summary


@pytest.mark.asyncio
async def test_settings_service_refreshes_timezone_bound_schedules_when_timezone_changes() -> None:
    repository = FakeSettingsRepository(GuildSettings.default(1))
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_settings(
        FakeGuild(1),  # type: ignore[arg-type]
        default_timezone="Europe/Berlin",
    )

    assert saved.default_timezone == "Europe/Berlin"
    assert repository.timezone_refresh_calls == [(1, "Europe/Berlin")]


@pytest.mark.asyncio
async def test_update_announcement_surface_deletes_sparse_row_when_all_overrides_are_cleared(
) -> None:
    repository = FakeSettingsRepository(
        GuildSettings.default(1),
        surfaces={
            "anniversary": AnnouncementSurfaceSettings(
                guild_id=1,
                surface_kind="anniversary",
                channel_id=456,
                image_url="https://cdn.example.com/anniversary.gif",
                thumbnail_url="https://cdn.example.com/anniversary-thumb.webp",
            )
        },
    )
    service = SettingsService(repository)  # type: ignore[arg-type]

    saved = await service.update_announcement_surface(
        FakeGuild(1),  # type: ignore[arg-type]
        surface_kind="anniversary",
        channel_id=None,
        image_url=None,
        thumbnail_url=None,
    )

    assert saved.channel_id is None
    assert saved.image_url is None
    assert saved.thumbnail_url is None
    assert "anniversary" not in repository.surfaces
