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
    party_prefix: str
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
        party_prefix="Celebrate:",
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
        party_prefix="Party time:",
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
        party_prefix="Now celebrating:",
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
        party_prefix="Sending love:",
        quiet_color=0xD16BA5,
        party_color=0xFF7BAC,
        footer_label="Cute",
    ),
    "elegant": AnnouncementThemeSpec(
        key="elegant",
        label="Elegant",
        description="Polished and understated server branding.",
        single_title="A special day",
        multi_title="Special days today",
        party_prefix="Honoring:",
        quiet_color=0x2C3E50,
        party_color=0xC8A96A,
        footer_label="Elegant",
    ),
    "gaming": AnnouncementThemeSpec(
        key="gaming",
        label="Gaming",
        description="Arcade-style celebration energy without excess noise.",
        single_title="Level up day",
        multi_title="Today's party queue",
        party_prefix="Queue up:",
        quiet_color=0x16A085,
        party_color=0x2ECC71,
        footer_label="Gaming",
    ),
}


def supported_announcement_themes() -> tuple[AnnouncementThemeSpec, ...]:
    return tuple(_THEME_SPECS.values())


def validate_announcement_theme(theme: AnnouncementTheme | str) -> AnnouncementTheme:
    if theme not in _THEME_SPECS:
        supported = ", ".join(spec.key for spec in supported_announcement_themes())
        raise ValueError(f"Unknown announcement theme '{theme}'. Choose one of: {supported}.")
    return theme  # type: ignore[return-value]


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
    title_override: str | None = None,
) -> str:
    if title_override:
        return title_override
    spec = _THEME_SPECS[theme]
    base = spec.single_title if recipient_count == 1 else spec.multi_title
    if celebration_mode == "party":
        return f"{spec.party_prefix} {base}"
    return base


def announcement_theme_color_value(
    theme: AnnouncementTheme,
    *,
    celebration_mode: CelebrationMode,
    accent_override: int | None = None,
) -> int:
    if accent_override is not None:
        return accent_override
    spec = _THEME_SPECS[theme]
    return spec.party_color if celebration_mode == "party" else spec.quiet_color


def announcement_theme_footer_label(theme: AnnouncementTheme) -> str:
    return _THEME_SPECS[theme].footer_label
