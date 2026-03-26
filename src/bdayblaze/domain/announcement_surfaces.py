from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar, cast

from bdayblaze.domain.models import (
    AnnouncementKind,
    AnnouncementSurfaceKind,
    AnnouncementSurfaceSettings,
    ResolvedAnnouncementSurface,
    ResolvedSurfaceField,
)

PUBLIC_ANNOUNCEMENT_SURFACES: tuple[AnnouncementSurfaceKind, ...] = (
    "birthday_announcement",
    "anniversary",
    "server_anniversary",
    "recurring_event",
)


def surface_label(kind: AnnouncementKind | AnnouncementSurfaceKind) -> str:
    return {
        "birthday_announcement": "Birthday announcement",
        "birthday_dm": "Birthday DM",
        "anniversary": "Member anniversary",
        "server_anniversary": "Server anniversary",
        "recurring_event": "Recurring annual event",
    }[kind]


def surface_source_label(
    source: str,
    *,
    surface_kind: AnnouncementSurfaceKind,
) -> str:
    if source == "custom":
        return f"Custom for {surface_label(surface_kind)}"
    if source == "event_override":
        return "Event override"
    if source.startswith("inherited:"):
        inherited_from = cast(AnnouncementKind, source.split(":", 1)[1])
        return f"Inherited from {surface_label(inherited_from)}"
    return "Not configured"


def normalize_announcement_surfaces(
    guild_id: int,
    surfaces: Mapping[AnnouncementSurfaceKind, AnnouncementSurfaceSettings],
) -> dict[AnnouncementSurfaceKind, AnnouncementSurfaceSettings]:
    return {
        surface_kind: surfaces.get(surface_kind)
        or AnnouncementSurfaceSettings.empty(guild_id, surface_kind)
        for surface_kind in PUBLIC_ANNOUNCEMENT_SURFACES
    }


def resolve_announcement_surface(
    guild_id: int,
    surface_kind: AnnouncementSurfaceKind,
    surfaces: Mapping[AnnouncementSurfaceKind, AnnouncementSurfaceSettings],
    *,
    event_channel_id: int | None = None,
) -> ResolvedAnnouncementSurface:
    normalized = normalize_announcement_surfaces(guild_id, surfaces)
    birthday_surface = normalized["birthday_announcement"]
    current_surface = normalized[surface_kind]

    if surface_kind == "birthday_announcement":
        return ResolvedAnnouncementSurface(
            surface_kind=surface_kind,
            channel=_resolve_surface_field(current_surface.channel_id),
            image=_resolve_surface_field(current_surface.image_url),
            thumbnail=_resolve_surface_field(current_surface.thumbnail_url),
        )

    if surface_kind == "anniversary":
        return ResolvedAnnouncementSurface(
            surface_kind=surface_kind,
            channel=_resolve_surface_field(
                current_surface.channel_id,
                fallbacks=(("birthday_announcement", birthday_surface.channel_id),),
            ),
            image=_resolve_surface_field(
                current_surface.image_url,
                fallbacks=(("birthday_announcement", birthday_surface.image_url),),
            ),
            thumbnail=_resolve_surface_field(
                current_surface.thumbnail_url,
                fallbacks=(("birthday_announcement", birthday_surface.thumbnail_url),),
            ),
        )

    return ResolvedAnnouncementSurface(
        surface_kind=surface_kind,
        channel=_resolve_surface_field(
            current_surface.channel_id,
            override_value=event_channel_id,
            fallbacks=(("birthday_announcement", birthday_surface.channel_id),),
        ),
        image=_resolve_surface_field(
            current_surface.image_url,
            fallbacks=(("birthday_announcement", birthday_surface.image_url),),
        ),
        thumbnail=_resolve_surface_field(
            current_surface.thumbnail_url,
            fallbacks=(("birthday_announcement", birthday_surface.thumbnail_url),),
        ),
    )


def has_surface_override(surface: AnnouncementSurfaceSettings) -> bool:
    return any(
        value is not None
        for value in (
            surface.channel_id,
            surface.image_url,
            surface.thumbnail_url,
        )
    )


def _resolve_surface_field(
    configured_value: T | None,
    *,
    override_value: T | None = None,
    fallbacks: Sequence[tuple[AnnouncementSurfaceKind, T | None]] = (),
) -> ResolvedSurfaceField[T]:
    if override_value is not None:
        return ResolvedSurfaceField(
            configured_value=configured_value,
            effective_value=override_value,
            source="event_override",
            override_value=override_value,
        )
    if configured_value is not None:
        return ResolvedSurfaceField(
            configured_value=configured_value,
            effective_value=configured_value,
            source="custom",
        )
    for fallback_kind, fallback_value in fallbacks:
        if fallback_value is None:
            continue
        return ResolvedSurfaceField(
            configured_value=configured_value,
            effective_value=fallback_value,
            source=f"inherited:{fallback_kind}",
        )
    return ResolvedSurfaceField(
        configured_value=configured_value,
        effective_value=None,
        source="unset",
        override_value=override_value,
    )


T = TypeVar("T", int, str)
