from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from bdayblaze.domain.models import AnnouncementTheme, CelebrationMode


@dataclass(slots=True, frozen=True)
class AnnouncementThemeSpec:
    key: AnnouncementTheme
    label: str
    description: str
    single_title: str
    multi_title: str
    emoji: str
    quiet_color: int
    party_color: int
    footer_label: str


_THEME_SPECS: Final[dict[AnnouncementTheme, AnnouncementThemeSpec]] = {
    "classic": AnnouncementThemeSpec(
        key="classic",
        label="Classic",
        description="Warm and familiar birthday posts.",
        single_title="Happy birthday",
        multi_title="Birthday crew",
        emoji="🎂",
        quiet_color=0x5865F2,
        party_color=0xF1C40F,
        footer_label="Classic",
    ),
    "festive": AnnouncementThemeSpec(
        key="festive",
        label="Festive",
        description="Bright, energetic celebration posts.",
        single_title="Celebrate today",
        multi_title="Celebration squad",
        emoji="🎉",
        quiet_color=0xE67E22,
        party_color=0xFF5A5F,
        footer_label="Festive",
    ),
    "minimal": AnnouncementThemeSpec(
        key="minimal",
        label="Minimal",
        description="Clean, low-noise announcement posts.",
        single_title="Birthday",
        multi_title="Today's birthdays",
        emoji="✨",
        quiet_color=0x95A5A6,
        party_color=0x3498DB,
        footer_label="Minimal",
    ),
    "cute": AnnouncementThemeSpec(
        key="cute",
        label="Cute",
        description="Soft, playful celebration posts.",
        single_title="Birthday wishes",
        multi_title="Birthday bunch",
        emoji="🧁",
        quiet_color=0xD16BA5,
        party_color=0xFF7BAC,
        footer_label="Cute",
    ),
}


def supported_announcement_themes() -> tuple[AnnouncementThemeSpec, ...]:
    return tuple(_THEME_SPECS.values())


def validate_announcement_theme(theme: AnnouncementTheme | str) -> AnnouncementTheme:
    if theme not in _THEME_SPECS:
        supported = ", ".join(spec.key for spec in supported_announcement_themes())
        raise ValueError(f"Unknown announcement theme '{theme}'. Choose one of: {supported}.")
    return theme


def announcement_theme_label(theme: AnnouncementTheme) -> str:
    return _THEME_SPECS[theme].label


def announcement_theme_description(theme: AnnouncementTheme) -> str:
    return _THEME_SPECS[theme].description


def announcement_theme_spec(theme: AnnouncementTheme) -> AnnouncementThemeSpec:
    return _THEME_SPECS[theme]


def announcement_theme_title(
    theme: AnnouncementTheme,
    *,
    recipient_count: int,
    celebration_mode: CelebrationMode,
) -> str:
    spec = _THEME_SPECS[theme]
    base = spec.single_title if recipient_count == 1 else spec.multi_title
    if celebration_mode == "party":
        return f"{spec.emoji} {base}"
    return base


def announcement_theme_color_value(
    theme: AnnouncementTheme,
    *,
    celebration_mode: CelebrationMode,
) -> int:
    spec = _THEME_SPECS[theme]
    return spec.party_color if celebration_mode == "party" else spec.quiet_color


def announcement_theme_footer_label(theme: AnnouncementTheme) -> str:
    return _THEME_SPECS[theme].footer_label
