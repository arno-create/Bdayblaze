from __future__ import annotations

from typing import Literal, TypeVar, cast
from urllib.parse import urlparse

from bdayblaze.domain.announcement_surfaces import surface_label
from bdayblaze.domain.media_validation import (
    assess_media_url,
    strip_validated_direct_media_marker,
)
from bdayblaze.domain.models import (
    AnnouncementKind,
    AnnouncementSurfaceKind,
    ResolvedAnnouncementSurface,
    ResolvedSurfaceField,
)

T = TypeVar("T")


def celebration_mode_summary(mode: str) -> str:
    return {
        "quiet": "Quiet: polished, restrained celebration energy",
        "party": "Party: brighter, more playful celebration energy",
    }.get(mode, mode.title())


def route_line(
    field: ResolvedSurfaceField[int],
    *,
    surface_kind: AnnouncementSurfaceKind,
) -> str:
    if field.effective_value is None:
        return "Route: not set"
    source_badge = field_source_badge(field, surface_kind=surface_kind)
    return f"Route: <#{field.effective_value}> ({source_badge})"


def route_source_line(
    field: ResolvedSurfaceField[int],
    *,
    surface_kind: AnnouncementSurfaceKind,
) -> str:
    if field.override_value is not None and field.configured_value is not None:
        return f"Surface default: <#{field.configured_value}>"
    return f"Route source: {field_source_badge(field, surface_kind=surface_kind)}"


def media_line(
    field: ResolvedSurfaceField[str],
    *,
    label: Literal["image", "thumbnail"],
    surface_kind: AnnouncementSurfaceKind,
) -> str:
    value = field.effective_value
    if value is None:
        return f"{label.title()}: not set"
    assessment = assess_media_url(value, label=label.title())
    if assessment is None:
        return f"{label.title()}: not set"
    state = _media_state_label(assessment.classification, normalized_url=assessment.normalized_url)
    extras: list[str] = [field_source_badge(field, surface_kind=surface_kind)]
    if assessment.classification in {"webpage", "unsupported_media", "invalid_or_unsafe"}:
        extras.append("needs attention")
    elif assessment.classification == "needs_validation":
        extras.append("review suggested")
    return f"{label.title()}: {state} ({', '.join(extras)})"


def media_health_line(surface: ResolvedAnnouncementSurface) -> str:
    assessments = [
        assessment
        for assessment in (
            assess_media_url(surface.image.effective_value, label="Image")
            if surface.image.effective_value is not None
            else None,
            assess_media_url(surface.thumbnail.effective_value, label="Thumbnail")
            if surface.thumbnail.effective_value is not None
            else None,
        )
        if assessment is not None
    ]
    if not assessments:
        return "Media health: no image or thumbnail saved"
    if any(
        assessment.classification in {"webpage", "unsupported_media", "invalid_or_unsafe"}
        for assessment in assessments
    ):
        return "Media health: needs attention"
    if any(assessment.classification == "needs_validation" for assessment in assessments):
        return "Media health: review suggested"
    return "Media health: ready"


def media_source_line(surface: ResolvedAnnouncementSurface) -> str:
    image_source = field_source_badge(surface.image, surface_kind=surface.surface_kind)
    thumbnail_source = field_source_badge(surface.thumbnail, surface_kind=surface.surface_kind)
    if image_source == thumbnail_source:
        return f"Media source: {image_source}"
    return f"Media source: mixed ({image_source} / {thumbnail_source})"


def surface_live_lines(surface: ResolvedAnnouncementSurface) -> tuple[str, ...]:
    return (
        route_line(surface.channel, surface_kind=surface.surface_kind),
        media_line(surface.image, label="image", surface_kind=surface.surface_kind),
        media_line(surface.thumbnail, label="thumbnail", surface_kind=surface.surface_kind),
        media_health_line(surface),
    )


def surface_detail_lines(surface: ResolvedAnnouncementSurface) -> tuple[str, ...]:
    return (
        route_source_line(surface.channel, surface_kind=surface.surface_kind),
        media_source_line(surface),
    )


def field_source_badge(
    field: ResolvedSurfaceField[T],
    *,
    surface_kind: AnnouncementSurfaceKind,
) -> str:
    if field.source == "custom":
        return "custom"
    if field.source == "event_override":
        return "event override"
    if field.source.startswith("inherited:"):
        inherited_from = cast(AnnouncementKind, field.source.split(":", 1)[1])
        if inherited_from == "birthday_announcement":
            return "inherits birthday default"
        return f"inherits {surface_label(inherited_from).lower()}"
    return "not set"


def media_state_badge(value: str | None, *, label: str) -> str:
    assessment = assess_media_url(value, label=label)
    if assessment is None:
        return "not set"
    return _media_state_label(
        assessment.classification,
        normalized_url=assessment.normalized_url,
    )


def _media_state_label(classification: str, *, normalized_url: str) -> str:
    if classification == "direct_media":
        stripped = strip_validated_direct_media_marker(normalized_url) or normalized_url
        path = urlparse(stripped).path.lower()
        if path.endswith(".gif"):
            return "direct GIF"
        if path.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return "direct image"
        return "direct media"
    return {
        "webpage": "webpage link",
        "unsupported_media": "unsupported file",
        "invalid_or_unsafe": "blocked URL",
        "needs_validation": "validation-needed URL",
    }.get(classification, "saved media")
