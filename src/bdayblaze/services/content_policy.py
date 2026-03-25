from __future__ import annotations

import re
from dataclasses import dataclass

from bdayblaze.services.errors import ValidationError

_LEETSPEAK_TRANSLATION = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)

_RULE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "profanity",
        "profane or vulgar language",
        re.compile(r"\b(?:asshole|bastard|bitch|bullshit|fuck|fucker|fucking|shit|wtf)\b"),
    ),
    (
        "explicit",
        "sexual or NSFW language",
        re.compile(
            r"\b(?:18\+|fetish|hentai|nsfw|nude|nudity|onlyfans|porn|porno|sex|sexy|xxx)\b"
        ),
    ),
    (
        "hateful",
        "hateful or abusive slurs",
        re.compile(
            r"\b(?:chink|fag|faggot|kike|nigga|nigger|spic|tranny)\b"
        ),
    ),
    (
        "harassment",
        "harassment or threat language",
        re.compile(
            r"\b(?:die in a fire|go die|hang yourself|kill yourself|kys|you deserve to die)\b"
        ),
    ),
)


@dataclass(slots=True, frozen=True)
class PolicyViolation:
    field_label: str
    rule_code: str
    category_label: str


class ContentPolicyError(ValidationError):
    def __init__(self, message: str, *, violations: tuple[PolicyViolation, ...]) -> None:
        super().__init__(message)
        self.violations = violations


def ensure_safe_text(value: str | None, *, label: str) -> None:
    if value is None:
        return
    normalized = _normalize_for_matching(value)
    if not normalized:
        return
    violations = tuple(
        PolicyViolation(
            field_label=label,
            rule_code=rule_code,
            category_label=category_label,
        )
        for rule_code, category_label, pattern in _RULE_PATTERNS
        if pattern.search(normalized)
    )
    if not violations:
        return
    categories = ", ".join(sorted({violation.category_label for violation in violations}))
    raise ContentPolicyError(
        f"{label} contains blocked {categories}.",
        violations=violations,
    )


def ensure_safe_event_name(value: str | None) -> None:
    ensure_safe_text(value, label="Recurring event name")


def ensure_safe_template(value: str | None, *, label: str) -> None:
    ensure_safe_text(value, label=label)


def ensure_safe_announcement_inputs(
    *,
    template: str | None,
    template_label: str,
    title_override: str | None,
    footer_text: str | None,
    event_name: str | None = None,
    event_name_label: str = "Recurring event name",
) -> None:
    ensure_safe_template(template, label=template_label)
    ensure_safe_text(title_override, label="Announcement title override")
    ensure_safe_text(footer_text, label="Announcement footer text")
    if event_name is not None:
        ensure_safe_text(event_name, label=event_name_label)


def combine_violations(*groups: tuple[PolicyViolation, ...]) -> tuple[PolicyViolation, ...]:
    combined: list[PolicyViolation] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for violation in group:
            key = (violation.field_label, violation.rule_code)
            if key in seen:
                continue
            seen.add(key)
            combined.append(violation)
    return tuple(combined)


def _normalize_for_matching(value: str) -> str:
    lowered = value.lower().translate(_LEETSPEAK_TRANSLATION)
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", normalized).strip()
