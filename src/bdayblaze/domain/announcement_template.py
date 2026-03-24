from __future__ import annotations

from calendar import month_name
from dataclasses import dataclass
from typing import Final, Literal

from bdayblaze.domain.models import CelebrationMode

MAX_TEMPLATE_LENGTH: Final = 500
DEFAULT_ANNOUNCEMENT_TEMPLATE: Final = (
    "Happy birthday {birthday.mentions}! Wishing you a great day in {server.name}."
)
MULTIPLE_TIMEZONES_LABEL: Final = "multiple timezones"
MULTIPLE_DATES_LABEL: Final = "multiple birthdays today"

PLACEHOLDER_DESCRIPTIONS: Final[dict[str, str]] = {
    "user.mention": (
        "Mention the birthday member. In batched posts, this becomes all birthday mentions."
    ),
    "user.display_name": (
        "Display name of the birthday member. In batched posts, this becomes the joined "
        "display names."
    ),
    "user.name": (
        "Username of the birthday member. In batched posts, this becomes the joined display names."
    ),
    "server.name": "The Discord server name.",
    "birthday.month": "Birthday month name. Falls back to 'multiple' when the batch mixes dates.",
    "birthday.day": "Birthday day number. Falls back to 'multiple' when the batch mixes dates.",
    "birthday.date": (
        "Readable birthday date like March 24. Falls back to 'multiple birthdays today' for "
        "mixed dates."
    ),
    "birthday.mentions": "All birthday mentions for the current announcement batch.",
    "birthday.names": "All birthday display names joined together for the current batch.",
    "birthday.count": "How many members are included in the current batch.",
    "timezone": "The shared birthday timezone, or 'multiple timezones' when the batch differs.",
    "celebration_mode": "The saved announcement style label for this server.",
}

_PLACEHOLDER_ORDER: Final[tuple[str, ...]] = tuple(PLACEHOLDER_DESCRIPTIONS)
_ALLOWED_PLACEHOLDERS: Final[frozenset[str]] = frozenset(_PLACEHOLDER_ORDER)


@dataclass(slots=True, frozen=True)
class AnnouncementRenderRecipient:
    mention: str
    display_name: str
    username: str
    birth_month: int
    birth_day: int
    timezone: str


@dataclass(slots=True, frozen=True)
class TemplateSegment:
    kind: Literal["text", "placeholder"]
    value: str


def supported_placeholders() -> tuple[tuple[str, str], ...]:
    return tuple(
        (placeholder, PLACEHOLDER_DESCRIPTIONS[placeholder]) for placeholder in _PLACEHOLDER_ORDER
    )


def celebration_mode_label(mode: CelebrationMode) -> str:
    return "Festive post" if mode == "party" else "Quiet post"


def normalize_announcement_template(template: str | None) -> str:
    if template is None:
        return DEFAULT_ANNOUNCEMENT_TEMPLATE
    stripped = template.strip()
    return stripped or DEFAULT_ANNOUNCEMENT_TEMPLATE


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


def render_announcement_template(
    template: str | None,
    *,
    server_name: str,
    celebration_mode: CelebrationMode,
    recipients: list[AnnouncementRenderRecipient],
) -> str:
    if not recipients:
        raise ValueError("At least one recipient is required to render an announcement.")
    normalized = normalize_announcement_template(template)
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

    mapping = _build_placeholder_values(
        server_name=server_name,
        celebration_mode=celebration_mode,
        recipients=recipients,
    )
    output: list[str] = []
    for segment in segments:
        if segment.kind == "text":
            output.append(segment.value)
            continue
        output.append(mapping[segment.value])
    return "".join(output)


def _build_placeholder_values(
    *,
    server_name: str,
    celebration_mode: CelebrationMode,
    recipients: list[AnnouncementRenderRecipient],
) -> dict[str, str]:
    first = recipients[0]
    same_date = all(
        recipient.birth_month == first.birth_month and recipient.birth_day == first.birth_day
        for recipient in recipients
    )
    same_timezone = all(recipient.timezone == first.timezone for recipient in recipients)
    names = ", ".join(recipient.display_name for recipient in recipients)
    mentions = " ".join(recipient.mention for recipient in recipients)
    single_user = len(recipients) == 1
    date_label = (
        _format_date(first.birth_month, first.birth_day) if same_date else MULTIPLE_DATES_LABEL
    )

    return {
        "user.mention": first.mention if single_user else mentions,
        "user.display_name": first.display_name if single_user else names,
        "user.name": first.username if single_user else names,
        "server.name": server_name,
        "birthday.month": month_name[first.birth_month] if same_date else "multiple",
        "birthday.day": str(first.birth_day) if same_date else "multiple",
        "birthday.date": date_label,
        "birthday.mentions": mentions,
        "birthday.names": names,
        "birthday.count": str(len(recipients)),
        "timezone": first.timezone if same_timezone else MULTIPLE_TIMEZONES_LABEL,
        "celebration_mode": celebration_mode_label(celebration_mode),
    }


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


def _format_date(month: int, day: int) -> str:
    return f"{month_name[month]} {day}"
