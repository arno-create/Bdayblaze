from __future__ import annotations

from bdayblaze.domain.announcement_surfaces import resolve_announcement_surface
from bdayblaze.domain.models import AnnouncementSurfaceSettings


def _surfaces() -> dict[str, AnnouncementSurfaceSettings]:
    return {
        "birthday_announcement": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="birthday_announcement",
            channel_id=123,
            image_url="https://cdn.example.com/birthday.gif",
            thumbnail_url="https://cdn.example.com/birthday-thumb.webp",
        ),
        "anniversary": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="anniversary",
            channel_id=456,
        ),
        "server_anniversary": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="server_anniversary",
            image_url="https://cdn.example.com/server.gif",
        ),
        "recurring_event": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="recurring_event",
            thumbnail_url="https://cdn.example.com/recurring-thumb.webp",
        ),
    }


def test_backfilled_birthday_and_anniversary_surfaces_preserve_existing_behavior() -> None:
    surfaces = _surfaces()

    birthday = resolve_announcement_surface(1, "birthday_announcement", surfaces)
    anniversary = resolve_announcement_surface(1, "anniversary", surfaces)

    assert birthday.channel.effective_value == 123
    assert birthday.image.effective_value == "https://cdn.example.com/birthday.gif"
    assert birthday.thumbnail.effective_value == "https://cdn.example.com/birthday-thumb.webp"
    assert birthday.channel.source == "custom"

    assert anniversary.channel.effective_value == 456
    assert anniversary.channel.source == "custom"
    assert anniversary.image.effective_value == "https://cdn.example.com/birthday.gif"
    assert anniversary.image.source == "inherited:birthday_announcement"
    assert anniversary.thumbnail.effective_value == "https://cdn.example.com/birthday-thumb.webp"
    assert anniversary.thumbnail.source == "inherited:birthday_announcement"


def test_server_anniversary_prefers_event_channel_override_but_keeps_surface_media() -> None:
    resolved = resolve_announcement_surface(
        1,
        "server_anniversary",
        _surfaces(),
        event_channel_id=999,
    )

    assert resolved.channel.configured_value is None
    assert resolved.channel.override_value == 999
    assert resolved.channel.effective_value == 999
    assert resolved.channel.source == "event_override"
    assert resolved.image.effective_value == "https://cdn.example.com/server.gif"
    assert resolved.image.source == "custom"
    assert (
        resolved.thumbnail.effective_value
        == "https://cdn.example.com/birthday-thumb.webp"
    )
    assert resolved.thumbnail.source == "inherited:birthday_announcement"


def test_recurring_event_inherits_route_and_uses_surface_thumbnail() -> None:
    resolved = resolve_announcement_surface(1, "recurring_event", _surfaces())

    assert resolved.channel.effective_value == 123
    assert resolved.channel.source == "inherited:birthday_announcement"
    assert resolved.image.effective_value == "https://cdn.example.com/birthday.gif"
    assert resolved.image.source == "inherited:birthday_announcement"
    assert (
        resolved.thumbnail.effective_value
        == "https://cdn.example.com/recurring-thumb.webp"
    )
    assert resolved.thumbnail.source == "custom"
