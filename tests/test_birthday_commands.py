from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bdayblaze.discord.cogs.birthday import (
    _build_preview_embed,
    _remove_active_birthday_role_if_needed,
    _require_ready_delivery,
    _visible_only_for_scope,
)
from bdayblaze.domain.announcement_template import server_anniversary_years_since_creation
from bdayblaze.domain.media_validation import mark_validated_direct_media_url
from bdayblaze.domain.models import (
    AnnouncementDeliveryReadiness,
    AnnouncementSurfaceSettings,
    GuildSettings,
    MemberBirthday,
    RecurringCelebration,
)
from bdayblaze.services.errors import ValidationError


class FakeSettingsService:
    def __init__(self, readiness: AnnouncementDeliveryReadiness) -> None:
        self.readiness = readiness

    async def describe_delivery(
        self,
        guild: object,
        *,
        kind: str,
        channel_id: int | None,
    ) -> AnnouncementDeliveryReadiness:
        return self.readiness


class FakeBirthdayService:
    def __init__(self) -> None:
        self.birthday = MemberBirthday(
            guild_id=1,
            user_id=42,
            birth_month=3,
            birth_day=24,
            birth_year=None,
            timezone_override="UTC",
            profile_visibility="private",
            next_occurrence_at_utc=datetime(2027, 3, 24, tzinfo=UTC),
            next_role_removal_at_utc=None,
            active_birthday_role_id=None,
        )
        self.recurring = RecurringCelebration(
            id=7,
            guild_id=1,
            name="Server birthday",
            event_month=3,
            event_day=25,
            channel_id=123,
            template="Today we celebrate {event.name}",
            enabled=True,
            next_occurrence_at_utc=datetime(2027, 3, 25, tzinfo=UTC),
        )
        self.server_anniversary = RecurringCelebration(
            id=8,
            guild_id=1,
            name="Server anniversary",
            event_month=4,
            event_day=1,
            channel_id=123,
            template="Happy {event.name}",
            enabled=True,
            next_occurrence_at_utc=datetime(2027, 4, 1, tzinfo=UTC),
            celebration_kind="server_anniversary",
            use_guild_created_date=False,
        )

    async def require_birthday(
        self,
        guild_id: int,
        user_id: int,
        *,
        missing_message: str,
    ) -> MemberBirthday:
        assert guild_id == 1
        assert user_id == 42
        return self.birthday

    async def get_recurring_celebration(
        self,
        guild_id: int,
        celebration_id: int,
    ) -> RecurringCelebration:
        assert guild_id == 1
        assert celebration_id == 7
        return self.recurring

    async def get_server_anniversary(self, guild_id: int) -> RecurringCelebration | None:
        assert guild_id == 1
        return self.server_anniversary


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, role: FakeRole) -> None:
        self.id = user_id
        self.name = "jamie"
        self.display_name = "Jamie"
        self.mention = "@Jamie"
        self.joined_at = datetime(2022, 3, 24, tzinfo=UTC)
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
        *,
        role: FakeRole | None = None,
        member: FakeMember | None = None,
    ) -> None:
        self.id = guild_id
        self.name = "Birthday Club"
        self._role = role
        self._member = member
        self.created_at = datetime(2020, 4, 1, tzinfo=UTC)

    def get_role(self, role_id: int) -> FakeRole | None:
        if self._role is not None and self._role.id == role_id:
            return self._role
        return None

    def get_member(self, user_id: int) -> FakeMember | None:
        if self._member is not None and self._member.id == user_id:
            return self._member
        return None

    async def fetch_member(self, user_id: int) -> FakeMember:
        raise AssertionError("fetch_member should not be used when the member is cached")


def _surfaces(
    *,
    image_url: str | None = None,
    thumbnail_url: str | None = None,
) -> dict[str, AnnouncementSurfaceSettings]:
    return {
        "birthday_announcement": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="birthday_announcement",
            channel_id=123,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        ),
        "anniversary": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="anniversary",
            channel_id=456,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        ),
        "server_anniversary": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="server_anniversary",
            channel_id=789,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        ),
        "recurring_event": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="recurring_event",
            channel_id=321,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        ),
    }


@pytest.mark.asyncio
async def test_build_preview_embed_uses_selected_member_for_anniversary_preview() -> None:
    service = FakeBirthdayService()
    member = FakeMember(42, FakeRole(55))
    settings = replace(
        GuildSettings.default(1),
        anniversary_enabled=True,
        anniversary_template=("Happy anniversary {members.names} for {anniversary.years} years."),
    )

    embed = await _build_preview_embed(
        FakeGuild(1),
        settings,
        service,  # type: ignore[arg-type]
        announcement_surfaces=_surfaces(),
        kind="anniversary",
        member=member,  # type: ignore[arg-type]
        event_id=None,
    )

    assert "Jamie" in embed.description
    assert "4 years" in embed.description


@pytest.mark.asyncio
async def test_build_preview_embed_uses_member_birthday_for_dm_preview() -> None:
    service = FakeBirthdayService()
    member = FakeMember(42, FakeRole(55))
    settings = replace(
        GuildSettings.default(1),
        birthday_dm_template="Happy birthday {user.display_name}",
        birthday_dm_enabled=True,
    )

    embed = await _build_preview_embed(
        FakeGuild(1),
        settings,
        service,  # type: ignore[arg-type]
        announcement_surfaces=_surfaces(),
        kind="birthday_dm",
        member=member,  # type: ignore[arg-type]
        event_id=None,
    )

    assert embed.description == "Happy birthday Jamie"


@pytest.mark.asyncio
async def test_build_preview_embed_ignores_shared_media_for_dm_preview() -> None:
    service = FakeBirthdayService()
    member = FakeMember(42, FakeRole(55))
    settings = replace(
        GuildSettings.default(1),
        birthday_dm_template="Happy birthday {user.display_name}",
        birthday_dm_enabled=True,
    )

    embed = await _build_preview_embed(
        FakeGuild(1),
        settings,
        service,  # type: ignore[arg-type]
        announcement_surfaces=_surfaces(
            image_url="https://cdn.example.com/not-image.pdf",
            thumbnail_url="https://cdn.example.com/also-bad.zip",
        ),
        kind="birthday_dm",
        member=member,  # type: ignore[arg-type]
        event_id=None,
    )

    assert embed.description == "Happy birthday Jamie"


@pytest.mark.asyncio
async def test_build_preview_embed_supports_server_anniversary_preview() -> None:
    service = FakeBirthdayService()
    settings = replace(GuildSettings.default(1), announcements_enabled=True)

    embed = await _build_preview_embed(
        FakeGuild(1),  # type: ignore[arg-type]
        settings,
        service,  # type: ignore[arg-type]
        announcement_surfaces=_surfaces(),
        kind="server_anniversary",
        member=None,
        event_id=None,
    )

    assert embed.title
    assert "Server anniversary" in embed.description


@pytest.mark.asyncio
async def test_build_preview_embed_renders_explicit_server_anniversary_years_placeholder() -> None:
    service = FakeBirthdayService()
    service.server_anniversary = replace(
        service.server_anniversary,
        template="Server age: {server_anniversary.years_since_creation}",
    )
    settings = replace(GuildSettings.default(1), announcements_enabled=True)
    guild = FakeGuild(1)

    embed = await _build_preview_embed(
        guild,  # type: ignore[arg-type]
        settings,
        service,  # type: ignore[arg-type]
        announcement_surfaces=_surfaces(),
        kind="server_anniversary",
        member=None,
        event_id=None,
    )

    expected_years = server_anniversary_years_since_creation(
        guild.created_at,
        now_utc=datetime.now(UTC),
    )
    assert f"Server age: {expected_years}" in (embed.description or "")


@pytest.mark.asyncio
async def test_build_preview_embed_rejects_member_anniversary_years_on_server_anniversary() -> None:
    service = FakeBirthdayService()
    service.server_anniversary = replace(
        service.server_anniversary,
        template="Server age: {anniversary.years}",
    )
    settings = replace(GuildSettings.default(1), announcements_enabled=True)

    with pytest.raises(
        ValueError,
        match=r"Use \{server_anniversary\.years_since_creation\} instead",
    ):
        await _build_preview_embed(
            FakeGuild(1),  # type: ignore[arg-type]
            settings,
            service,  # type: ignore[arg-type]
            announcement_surfaces=_surfaces(),
            kind="server_anniversary",
            member=None,
            event_id=None,
        )


@pytest.mark.asyncio
async def test_build_preview_embed_surfaces_invalid_saved_media() -> None:
    service = FakeBirthdayService()
    settings = replace(
        GuildSettings.default(1),
        announcements_enabled=True,
    )

    with pytest.raises(ValueError, match="unsupported \\.pdf content"):
        await _build_preview_embed(
            FakeGuild(1),
            settings,
            service,  # type: ignore[arg-type]
            announcement_surfaces=_surfaces(image_url="https://cdn.example.com/notes.pdf"),
            kind="birthday_announcement",
            member=None,
            event_id=None,
        )


@pytest.mark.asyncio
async def test_build_preview_embed_accepts_validated_extensionless_media() -> None:
    service = FakeBirthdayService()
    settings = replace(
        GuildSettings.default(1),
        announcements_enabled=True,
    )

    embed = await _build_preview_embed(
        FakeGuild(1),
        settings,
        service,  # type: ignore[arg-type]
        announcement_surfaces=_surfaces(
            image_url=mark_validated_direct_media_url(
                "https://cdn.example.com/assets/banner?sig=abc123"
            )
        ),
        kind="birthday_announcement",
        member=None,
        event_id=None,
    )

    assert embed.description


@pytest.mark.asyncio
async def test_build_preview_embed_blocks_unsafe_anniversary_copy() -> None:
    service = FakeBirthdayService()
    settings = replace(
        GuildSettings.default(1),
        announcements_enabled=True,
        anniversary_template="Happy anniversary, go die",
    )

    with pytest.raises(ValidationError, match="blocked harassment or threat language"):
        await _build_preview_embed(
            FakeGuild(1),
            settings,
            service,  # type: ignore[arg-type]
            announcement_surfaces=_surfaces(),
            kind="anniversary",
            member=None,
            event_id=None,
        )


@pytest.mark.asyncio
async def test_require_ready_delivery_raises_on_blocked_readiness() -> None:
    settings_service = FakeSettingsService(
        AnnouncementDeliveryReadiness(
            status="blocked",
            summary="Blocked",
            details=("The bot cannot send messages in #birthdays.",),
        )
    )

    with pytest.raises(ValidationError, match="cannot send messages"):
        await _require_ready_delivery(
            settings_service,  # type: ignore[arg-type]
            FakeGuild(1),  # type: ignore[arg-type]
            kind="recurring_event",
            channel_id=123,
        )


@pytest.mark.asyncio
async def test_remove_active_birthday_role_if_needed_removes_saved_role() -> None:
    role = FakeRole(55)
    member = FakeMember(42, role)
    guild = FakeGuild(1, role=role, member=member)

    await _remove_active_birthday_role_if_needed(
        guild,  # type: ignore[arg-type]
        42,
        55,
        reason="Birthday cleanup",
    )

    assert member.removed_reasons == ["Birthday cleanup"]


def test_visible_only_for_scope_rejects_non_admin_all_scope() -> None:
    interaction = SimpleNamespace(permissions=SimpleNamespace(manage_guild=False))

    with pytest.raises(ValidationError, match="Only admins"):
        _visible_only_for_scope(interaction, "all")  # type: ignore[arg-type]
