from __future__ import annotations

from calendar import month_name
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Final, Literal

from bdayblaze.domain.birthday_logic import LATE_CELEBRATION_NOTE
from bdayblaze.domain.media_validation import validate_direct_media_url
from bdayblaze.domain.models import (
    AnnouncementKind,
    AnnouncementStudioPresentation,
    CelebrationMode,
)

MAX_TEMPLATE_LENGTH: Final = 1200
MAX_TITLE_LENGTH: Final = 256
MAX_FOOTER_LENGTH: Final = 512
DEFAULT_ANNOUNCEMENT_TEMPLATE: Final = (
    "Happy birthday {birthday.mentions}! Wishing you a great day in {server.name}."
)
DEFAULT_DM_TEMPLATE: Final = (
    "Happy birthday {user.display_name}! Your celebration is live in {server.name}."
)
DEFAULT_ANNIVERSARY_TEMPLATE: Final = (
    "Happy anniversary to {members.mentions}! Thanks for being part of {server.name}."
)
DEFAULT_RECURRING_EVENT_TEMPLATE: Final = "Today we are celebrating {event.name} in {server.name}."
MULTIPLE_TIMEZONES_LABEL: Final = "multiple timezones"
MULTIPLE_DATES_LABEL: Final = "multiple celebration dates"
MULTIPLE_YEARS_LABEL: Final = "multiple milestone years"

PLACEHOLDER_DESCRIPTIONS: Final[dict[str, str]] = {
    "user.mention": "Mention the celebration member. In batches, this becomes all mentions.",
    "user.display_name": "Display name of the member. In batches, this becomes joined names.",
    "user.name": "Username of the member. In batches, this becomes joined names.",
    "members.mentions": "All member mentions for the current delivery.",
    "members.names": "All member display names for the current delivery.",
    "members.count": "How many members are included in the current delivery.",
    "server.name": "The Discord server name.",
    "birthday.month": "Birthday month name, or 'multiple' for mixed dates.",
    "birthday.day": "Birthday day number, or 'multiple' for mixed dates.",
    "birthday.date": "Readable birthday date like March 24, or a mixed-date label.",
    "birthday.mentions": "All birthday mentions for the current delivery.",
    "birthday.names": "All birthday display names for the current delivery.",
    "birthday.count": "How many birthday members are included in the delivery.",
    "timezone": "The shared timezone, or 'multiple timezones' when the batch differs.",
    "celebration_mode": "The saved celebration style label for this server.",
    "delivery.note": "Late-delivery note when recovery sends a celebration after its exact start.",
    "event.name": "The recurring or anniversary event name when applicable.",
    "event.date": "Readable event date like March 24 when applicable.",
    "event.kind": "The event kind label, such as birthday or anniversary.",
    "anniversary.years": "Years since the member joined, or a mixed-years label in batches.",
}

_PLACEHOLDER_ORDER: Final[tuple[str, ...]] = tuple(PLACEHOLDER_DESCRIPTIONS)
_ALLOWED_PLACEHOLDERS: Final[frozenset[str]] = frozenset(_PLACEHOLDER_ORDER)
PLACEHOLDER_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "Birthday and member fields": (
        "user.mention",
        "user.display_name",
        "user.name",
        "members.mentions",
        "members.names",
        "members.count",
        "birthday.month",
        "birthday.day",
        "birthday.date",
        "birthday.mentions",
        "birthday.names",
        "birthday.count",
        "timezone",
    ),
    "Server and delivery fields": (
        "server.name",
        "celebration_mode",
        "delivery.note",
    ),
    "Anniversary and event fields": (
        "event.name",
        "event.date",
        "event.kind",
        "anniversary.years",
    ),
}


@dataclass(slots=True, frozen=True)
class AnnouncementRenderRecipient:
    mention: str
    display_name: str
    username: str
    birth_month: int | None = None
    birth_day: int | None = None
    timezone: str | None = None
    anniversary_years: int | None = None


@dataclass(slots=True, frozen=True)
class AnnouncementRenderContext:
    kind: AnnouncementKind
    server_name: str
    celebration_mode: CelebrationMode
    recipients: list[AnnouncementRenderRecipient]
    event_name: str | None = None
    event_month: int | None = None
    event_day: int | None = None
    late_delivery: bool = False


@dataclass(slots=True, frozen=True)
class TemplateSegment:
    kind: Literal["text", "placeholder"]
    value: str


def supported_placeholders() -> tuple[tuple[str, str], ...]:
    return tuple(
        (placeholder, PLACEHOLDER_DESCRIPTIONS[placeholder]) for placeholder in _PLACEHOLDER_ORDER
    )


def supported_placeholder_groups() -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    return tuple(
        (
            group_name,
            tuple(
                (placeholder, PLACEHOLDER_DESCRIPTIONS[placeholder]) for placeholder in placeholders
            ),
        )
        for group_name, placeholders in PLACEHOLDER_GROUPS.items()
    )


def celebration_mode_label(mode: CelebrationMode) -> str:
    return "Festive post" if mode == "party" else "Quiet post"


def default_template_for_kind(kind: AnnouncementKind) -> str:
    if kind == "birthday_dm":
        return DEFAULT_DM_TEMPLATE
    if kind == "anniversary":
        return DEFAULT_ANNIVERSARY_TEMPLATE
    if kind == "recurring_event":
        return DEFAULT_RECURRING_EVENT_TEMPLATE
    if kind == "server_anniversary":
        return "Today we are celebrating {event.name} in {server.name}."
    return DEFAULT_ANNOUNCEMENT_TEMPLATE


def normalize_announcement_template(template: str | None, *, kind: AnnouncementKind) -> str:
    if template is None:
        return default_template_for_kind(kind)
    stripped = template.strip()
    return stripped or default_template_for_kind(kind)


def validate_announcement_template(template: str | None) -> str | None:
    if template is None:
        return None
    normalized = template.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_TEMPLATE_LENGTH:
        raise ValueError(
            f"Announcement messages must be {MAX_TEMPLATE_LENGTH} characters or fewer."
        )
    segments = _parse_template_segments(normalized)
    unknown = sorted(
        {
            segment.value
            for segment in segments
            if segment.kind == "placeholder" and segment.value not in _ALLOWED_PLACEHOLDERS
        }
    )
    if unknown:
        formatted = ", ".join(f"{{{token}}}" for token in unknown)
        raise ValueError(f"Unknown placeholder(s): {formatted}")
    return normalized


def validate_studio_text(value: str | None, *, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise ValueError(f"{label} must be {max_length} characters or fewer.")
    return normalized


def validate_media_url(value: str | None, *, label: str) -> str | None:
    return validate_direct_media_url(value, label=label)


def validate_announcement_presentation(
    presentation: AnnouncementStudioPresentation,
) -> AnnouncementStudioPresentation:
    return replace(
        presentation,
        image_url=validate_media_url(presentation.image_url, label="Announcement image"),
        thumbnail_url=validate_media_url(
            presentation.thumbnail_url,
            label="Announcement thumbnail",
        ),
    )


def validate_accent_color(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    candidate = normalized[1:] if normalized.startswith("#") else normalized
    if len(candidate) != 6 or any(
        character not in "0123456789abcdefABCDEF" for character in candidate
    ):
        raise ValueError("Accent color must be a 6-digit hex value like #FFB347.")
    return int(candidate, 16)


def render_announcement_template(
    template: str | None,
    *,
    context: AnnouncementRenderContext,
) -> str:
    normalized = normalize_announcement_template(template, kind=context.kind)
    segments = _parse_template_segments(normalized)
    unknown = sorted(
        {
            segment.value
            for segment in segments
            if segment.kind == "placeholder" and segment.value not in _ALLOWED_PLACEHOLDERS
        }
    )
    if unknown:
        formatted = ", ".join(f"{{{token}}}" for token in unknown)
        raise ValueError(f"Unknown placeholder(s): {formatted}")

    mapping = _build_placeholder_values(context=context)
    output: list[str] = []
    for segment in segments:
        if segment.kind == "text":
            output.append(segment.value)
            continue
        output.append(mapping[segment.value])
    return "".join(output).strip()


def _build_placeholder_values(*, context: AnnouncementRenderContext) -> dict[str, str]:
    recipients = context.recipients
    first = recipients[0] if recipients else None
    if first is None:
        same_date = False
        same_timezone = False
        same_anniversary_years = False
        first_birth_month = None
        first_birth_day = None
        first_timezone = None
        first_anniversary_years = None
    else:
        same_date = all(
            recipient.birth_month == first.birth_month
            and recipient.birth_day == first.birth_day
            for recipient in recipients
        )
        same_timezone = all(recipient.timezone == first.timezone for recipient in recipients)
        same_anniversary_years = all(
            recipient.anniversary_years == first.anniversary_years
            for recipient in recipients
        )
        first_birth_month = first.birth_month
        first_birth_day = first.birth_day
        first_timezone = first.timezone
        first_anniversary_years = first.anniversary_years
    names = ", ".join(recipient.display_name for recipient in recipients) or "everyone"
    mentions = " ".join(recipient.mention for recipient in recipients)
    single_user = len(recipients) == 1 and first is not None
    birthday_date = (
        _format_date(first_birth_month, first_birth_day)
        if same_date and first_birth_month is not None and first_birth_day is not None
        else MULTIPLE_DATES_LABEL
    )
    event_date = (
        _format_date(context.event_month, context.event_day)
        if context.event_month is not None and context.event_day is not None
        else birthday_date
    )
    delivery_note = (
        LATE_CELEBRATION_NOTE if context.late_delivery else ""
    )
    event_kind_label = {
        "birthday_announcement": "birthday",
        "birthday_dm": "birthday",
        "anniversary": "anniversary",
        "server_anniversary": "server anniversary",
        "recurring_event": "recurring event",
    }[context.kind]
    anniversary_years = (
        str(first_anniversary_years)
        if same_anniversary_years and first_anniversary_years is not None
        else MULTIPLE_YEARS_LABEL
    )

    return {
        "user.mention": first.mention if single_user and first is not None else mentions,
        "user.display_name": first.display_name if single_user and first is not None else names,
        "user.name": first.username if single_user and first is not None else names,
        "members.mentions": mentions,
        "members.names": names,
        "members.count": str(len(recipients)),
        "server.name": context.server_name,
        "birthday.month": (
            month_name[first_birth_month]
            if same_date and first_birth_month is not None
            else "multiple"
        ),
        "birthday.day": (
            str(first_birth_day)
            if same_date and first_birth_day is not None
            else "multiple"
        ),
        "birthday.date": birthday_date,
        "birthday.mentions": mentions,
        "birthday.names": names,
        "birthday.count": str(len(recipients)),
        "timezone": (
            first_timezone
            if same_timezone and first_timezone is not None
            else MULTIPLE_TIMEZONES_LABEL
        ),
        "celebration_mode": celebration_mode_label(context.celebration_mode),
        "delivery.note": delivery_note,
        "event.name": context.event_name or event_kind_label.title(),
        "event.date": event_date,
        "event.kind": event_kind_label,
        "anniversary.years": anniversary_years,
    }


def anniversary_years(joined_at_utc: datetime, *, now_utc: datetime) -> int:
    joined_date = joined_at_utc.astimezone(UTC).date()
    current_date = now_utc.astimezone(UTC).date()
    years = current_date.year - joined_date.year
    if (current_date.month, current_date.day) < (joined_date.month, joined_date.day):
        return max(0, years - 1)
    return max(0, years)


def _parse_template_segments(template: str) -> list[TemplateSegment]:
    segments: list[TemplateSegment] = []
    text_buffer: list[str] = []
    index = 0
    while index < len(template):
        character = template[index]
        if character == "}" and not template.startswith("}}", index):
            raise ValueError("Templates cannot contain unmatched '}' characters.")
        if template.startswith("{{", index):
            text_buffer.append("{")
            index += 2
            continue
        if template.startswith("}}", index):
            text_buffer.append("}")
            index += 2
            continue
        if character != "{":
            text_buffer.append(character)
            index += 1
            continue

        if text_buffer:
            segments.append(TemplateSegment(kind="text", value="".join(text_buffer)))
            text_buffer.clear()
        end = template.find("}", index + 1)
        if end == -1:
            raise ValueError("Templates cannot contain unmatched '{' characters.")
        token = template[index + 1 : end].strip()
        if not token or "{" in token or "}" in token:
            raise ValueError(
                "Templates can only use plain text and full placeholders like {birthday.mentions}."
            )
        segments.append(TemplateSegment(kind="placeholder", value=token))
        index = end + 1
    if text_buffer:
        segments.append(TemplateSegment(kind="text", value="".join(text_buffer)))
    return segments


def _format_date(month: int | None, day: int | None) -> str:
    if month is None or day is None:
        return MULTIPLE_DATES_LABEL
    return f"{month_name[month]} {day}"


def preview_context_for_kind(kind: AnnouncementKind) -> AnnouncementRenderContext:
    preview_now = datetime(2026, 3, 25, tzinfo=UTC)
    if kind == "birthday_dm":
        return AnnouncementRenderContext(
            kind=kind,
            server_name="Bdayblaze HQ",
            celebration_mode="quiet",
            recipients=[
                AnnouncementRenderRecipient(
                    mention="@Jamie",
                    display_name="Jamie",
                    username="jamie",
                    birth_month=3,
                    birth_day=25,
                    timezone="Asia/Yerevan",
                )
            ],
        )
    if kind == "anniversary":
        return AnnouncementRenderContext(
            kind=kind,
            server_name="Bdayblaze HQ",
            celebration_mode="quiet",
            recipients=[
                AnnouncementRenderRecipient(
                    mention="@Jamie",
                    display_name="Jamie",
                    username="jamie",
                    anniversary_years=2,
                ),
                AnnouncementRenderRecipient(
                    mention="@Rin",
                    display_name="Rin",
                    username="rin",
                    anniversary_years=4,
                ),
            ],
            event_name="Join anniversary",
            event_month=preview_now.month,
            event_day=preview_now.day,
        )
    if kind == "recurring_event":
        return AnnouncementRenderContext(
            kind=kind,
            server_name="Bdayblaze HQ",
            celebration_mode="party",
            recipients=[],
            event_name="Server birthday",
            event_month=3,
            event_day=25,
        )
    if kind == "server_anniversary":
        return AnnouncementRenderContext(
            kind=kind,
            server_name="Bdayblaze HQ",
            celebration_mode="party",
            recipients=[],
            event_name="Server anniversary",
            event_month=3,
            event_day=25,
        )
    return AnnouncementRenderContext(
        kind=kind,
        server_name="Bdayblaze HQ",
        celebration_mode="party",
        recipients=[
            AnnouncementRenderRecipient(
                mention="@Jamie",
                display_name="Jamie",
                username="jamie",
                birth_month=3,
                birth_day=25,
                timezone="Asia/Yerevan",
            ),
            AnnouncementRenderRecipient(
                mention="@Rin",
                display_name="Rin",
                username="rin",
                birth_month=3,
                birth_day=25,
                timezone="Europe/Berlin",
            ),
        ],
    )


def celebration_date_for_occurrence(occurrence_at_utc: datetime) -> date:
    return occurrence_at_utc.astimezone(UTC).date()
