from __future__ import annotations

from bdayblaze.domain.announcement_surfaces import resolve_announcement_surface
from bdayblaze.domain.models import AnnouncementSurfaceSettings
from bdayblaze.domain.operator_summary import (
    celebration_mode_summary,
    media_health_line,
    media_line,
    route_line,
    route_source_line,
)


def _surface_settings(
    surface_kind: str,
    *,
    channel_id: int | None = None,
    image_url: str | None = None,
    thumbnail_url: str | None = None,
) -> AnnouncementSurfaceSettings:
    return AnnouncementSurfaceSettings(
        guild_id=1,
        surface_kind=surface_kind,  # type: ignore[arg-type]
        channel_id=channel_id,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
    )


def test_operator_summary_formats_custom_route_and_direct_gif() -> None:
    surface = resolve_announcement_surface(
        1,
        "birthday_announcement",
        {
            "birthday_announcement": _surface_settings(
                "birthday_announcement",
                channel_id=123,
                image_url="https://cdn.example.com/party.gif",
                thumbnail_url="https://cdn.example.com/thumb.webp",
            )
        },
    )

    assert route_line(
        surface.channel,
        surface_kind=surface.surface_kind,
    ) == "Route: <#123> (custom)"
    assert route_source_line(
        surface.channel,
        surface_kind=surface.surface_kind,
    ) == "Route source: custom"
    assert media_line(surface.image, label="image", surface_kind=surface.surface_kind) == (
        "Image: direct GIF (custom)"
    )
    assert media_line(surface.thumbnail, label="thumbnail", surface_kind=surface.surface_kind) == (
        "Thumbnail: direct image (custom)"
    )
    assert media_health_line(surface) == "Media health: ready"


def test_operator_summary_formats_inherited_route_and_media() -> None:
    surfaces = {
        "birthday_announcement": _surface_settings(
            "birthday_announcement",
            channel_id=123,
            image_url="https://cdn.example.com/banner.png",
            thumbnail_url="https://cdn.example.com/thumb.webp",
        ),
        "anniversary": _surface_settings("anniversary"),
    }
    surface = resolve_announcement_surface(1, "anniversary", surfaces)

    assert route_line(surface.channel, surface_kind=surface.surface_kind) == (
        "Route: <#123> (inherits birthday default)"
    )
    assert route_source_line(surface.channel, surface_kind=surface.surface_kind) == (
        "Route source: inherits birthday default"
    )
    assert media_line(surface.image, label="image", surface_kind=surface.surface_kind) == (
        "Image: direct image (inherits birthday default)"
    )


def test_operator_summary_formats_event_override_route() -> None:
    surfaces = {
        "birthday_announcement": _surface_settings("birthday_announcement", channel_id=123),
        "recurring_event": _surface_settings("recurring_event", channel_id=456),
    }
    surface = resolve_announcement_surface(
        1,
        "recurring_event",
        surfaces,
        event_channel_id=789,
    )

    assert route_line(surface.channel, surface_kind=surface.surface_kind) == (
        "Route: <#789> (event override)"
    )
    assert route_source_line(surface.channel, surface_kind=surface.surface_kind) == (
        "Surface default: <#456>"
    )


def test_operator_summary_handles_unset_media() -> None:
    surface = resolve_announcement_surface(
        1,
        "birthday_announcement",
        {"birthday_announcement": _surface_settings("birthday_announcement")},
    )

    assert (
        media_line(
            surface.image,
            label="image",
            surface_kind=surface.surface_kind,
        )
        == "Image: not set"
    )
    assert media_health_line(surface) == "Media health: no image or thumbnail saved"


def test_operator_summary_keeps_invalid_custom_media_explicit() -> None:
    surface = resolve_announcement_surface(
        1,
        "birthday_announcement",
        {
            "birthday_announcement": _surface_settings(
                "birthday_announcement",
                image_url="https://example.com/manual.pdf",
            )
        },
    )

    assert media_line(surface.image, label="image", surface_kind=surface.surface_kind) == (
        "Image: unsupported file (custom, needs attention)"
    )
    assert media_health_line(surface) == "Media health: needs attention"


def test_operator_summary_productizes_celebration_mode() -> None:
    assert celebration_mode_summary("quiet") == "Quiet: polished, restrained celebration energy"
    assert celebration_mode_summary("party") == "Party: brighter, more playful celebration energy"
