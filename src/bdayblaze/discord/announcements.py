from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import discord

from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    AnnouncementRenderRecipient,
    render_announcement_template,
)
from bdayblaze.domain.announcement_theme import (
    announcement_theme_color_value,
    announcement_theme_footer_label,
    announcement_theme_title,
)
from bdayblaze.domain.models import AnnouncementTheme, CelebrationMode


@dataclass(slots=True, frozen=True)
class PreparedAnnouncement:
    content: str
    embed: discord.Embed


_PREVIEW_SINGLE_RECIPIENTS: Final[tuple[AnnouncementRenderRecipient, ...]] = (
    AnnouncementRenderRecipient(
        mention="@Jamie",
        display_name="Jamie",
        username="jamie",
        birth_month=3,
        birth_day=24,
        timezone="Asia/Yerevan",
    ),
)

_PREVIEW_BATCH_RECIPIENTS: Final[tuple[AnnouncementRenderRecipient, ...]] = (
    AnnouncementRenderRecipient(
        mention="@Jamie",
        display_name="Jamie",
        username="jamie",
        birth_month=3,
        birth_day=24,
        timezone="Asia/Yerevan",
    ),
    AnnouncementRenderRecipient(
        mention="@Rin",
        display_name="Rin",
        username="rin",
        birth_month=3,
        birth_day=24,
        timezone="Europe/Berlin",
    ),
)


def build_announcement_message(
    *,
    server_name: str,
    recipients: list[AnnouncementRenderRecipient],
    celebration_mode: CelebrationMode,
    announcement_theme: AnnouncementTheme,
    template: str | None,
    batch_token: str | None = None,
    preview_label: str | None = None,
) -> PreparedAnnouncement:
    try:
        description = render_announcement_template(
            template,
            server_name=server_name,
            celebration_mode=celebration_mode,
            recipients=recipients,
        )
    except ValueError:
        description = render_announcement_template(
            DEFAULT_ANNOUNCEMENT_TEMPLATE,
            server_name=server_name,
            celebration_mode=celebration_mode,
            recipients=recipients,
        )

    embed = discord.Embed(
        title=announcement_theme_title(
            announcement_theme,
            recipient_count=len(recipients),
            celebration_mode=celebration_mode,
        ),
        description=description,
        color=discord.Color(
            announcement_theme_color_value(
                announcement_theme,
                celebration_mode=celebration_mode,
            )
        ),
    )
    if preview_label is not None:
        embed.set_author(name=preview_label)
        embed.set_footer(
            text=f"Bdayblaze Preview | {announcement_theme_footer_label(announcement_theme)}"
        )
    elif batch_token is not None:
        embed.set_footer(text=batch_footer(announcement_theme, batch_token))

    return PreparedAnnouncement(
        content=" ".join(recipient.mention for recipient in recipients),
        embed=embed,
    )


def batch_footer(theme: AnnouncementTheme, batch_token: str) -> str:
    return f"Bdayblaze {announcement_theme_footer_label(theme)} | {batch_token}"


def preview_single_recipients() -> list[AnnouncementRenderRecipient]:
    return list(_PREVIEW_SINGLE_RECIPIENTS)


def preview_batch_recipients() -> list[AnnouncementRenderRecipient]:
    return list(_PREVIEW_BATCH_RECIPIENTS)
