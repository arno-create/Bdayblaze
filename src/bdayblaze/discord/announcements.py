from __future__ import annotations

from dataclasses import dataclass

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed, truncate_text
from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    AnnouncementRenderContext,
    AnnouncementRenderRecipient,
    default_template_for_kind,
    render_announcement_template,
    validate_announcement_presentation,
)
from bdayblaze.domain.announcement_theme import (
    announcement_theme_color_value,
    announcement_theme_footer_label,
    announcement_theme_title,
)
from bdayblaze.domain.birthday_logic import LATE_CELEBRATION_NOTE
from bdayblaze.domain.models import (
    AnnouncementKind,
    AnnouncementStudioPresentation,
    AnnouncementTheme,
    BirthdayWish,
    CelebrationMode,
)


@dataclass(slots=True, frozen=True)
class PreparedAnnouncement:
    content: str
    embed: discord.Embed


@dataclass(slots=True, frozen=True)
class PreparedCapsuleReveal:
    content: str
    embeds: tuple[discord.Embed, ...]


def build_announcement_message(
    *,
    kind: AnnouncementKind,
    server_name: str,
    recipients: list[AnnouncementRenderRecipient],
    celebration_mode: CelebrationMode,
    announcement_theme: AnnouncementTheme,
    presentation: AnnouncementStudioPresentation,
    template: str | None,
    batch_token: str | None = None,
    preview_label: str | None = None,
    event_name: str | None = None,
    event_month: int | None = None,
    event_day: int | None = None,
    late_delivery: bool = False,
    mention_suppressed: bool = False,
) -> PreparedAnnouncement:
    validated_presentation = validate_announcement_presentation(presentation)
    context = AnnouncementRenderContext(
        kind=kind,
        server_name=server_name,
        celebration_mode=celebration_mode,
        recipients=recipients,
        event_name=event_name,
        event_month=event_month,
        event_day=event_day,
        late_delivery=late_delivery,
    )
    try:
        description = render_announcement_template(template, context=context)
    except ValueError:
        description = render_announcement_template(
            DEFAULT_ANNOUNCEMENT_TEMPLATE
            if kind == "birthday_announcement"
            else default_template_for_kind(kind),
            context=context,
        )
    recovery_note = LATE_CELEBRATION_NOTE
    if late_delivery and recovery_note not in description:
        description = f"{recovery_note}\n\n{description}".strip()

    budget = BudgetedEmbed.create(
        title=announcement_theme_title(
            announcement_theme,
            recipient_count=max(len(recipients), 1),
            celebration_mode=celebration_mode,
            title_override=validated_presentation.title_override,
        ),
        description=description,
        color=discord.Color(
            announcement_theme_color_value(
                announcement_theme,
                celebration_mode=celebration_mode,
                accent_override=validated_presentation.accent_color,
            )
        ),
    )
    embed = budget.build()
    if validated_presentation.image_url:
        embed.set_image(url=validated_presentation.image_url)
    if validated_presentation.thumbnail_url:
        embed.set_thumbnail(url=validated_presentation.thumbnail_url)
    footer_parts: list[str] = []
    if validated_presentation.footer_text:
        footer_parts.append(validated_presentation.footer_text)
    if preview_label is not None:
        budget.set_author(preview_label)
        footer_parts.append(
            f"Bdayblaze Preview | {announcement_theme_footer_label(announcement_theme)}"
        )
    elif batch_token is not None:
        footer_parts.append(batch_footer(announcement_theme, batch_token))
    elif kind == "birthday_dm":
        footer_parts.append("Bdayblaze Birthday DM")
    if footer_parts:
        budget.set_footer(" | ".join(footer_parts))

    mentions = " ".join(recipient.mention for recipient in recipients if recipient.mention)
    return PreparedAnnouncement(
        content="" if mention_suppressed else mentions,
        embed=budget.build(),
    )


def build_capsule_reveal_message(
    *,
    birthday_member: AnnouncementRenderRecipient,
    wishes: list[tuple[AnnouncementRenderRecipient | None, BirthdayWish]],
    celebration_mode: CelebrationMode,
    announcement_theme: AnnouncementTheme,
    late_delivery: bool = False,
) -> PreparedCapsuleReveal:
    visible_wishes = wishes[:12]
    overflow_count = max(0, len(wishes) - len(visible_wishes))
    embeds: list[discord.Embed] = []
    for index in range(0, len(visible_wishes), 6):
        chunk = visible_wishes[index : index + 6]
        intro = (
            f"{birthday_member.display_name}'s Birthday Capsule is open."
            "\nPrivate wishes queued ahead of the day are unlocking now."
        )
        if late_delivery:
            intro = f"{LATE_CELEBRATION_NOTE}\n\n{intro}"
        budget = BudgetedEmbed.create(
            title=(
                f"{birthday_member.display_name}'s Birthday Capsule"
                if index == 0
                else "Birthday Capsule"
            ),
            description=intro if index == 0 else "More unlocked wishes:",
            color=discord.Color(
                announcement_theme_color_value(
                    announcement_theme,
                    celebration_mode=celebration_mode,
                    accent_override=None,
                )
            ),
        )
        for author, wish in chunk:
            author_name = author.display_name if author is not None else "A friend"
            lines = [truncate_text(wish.wish_text, 700)]
            if wish.link_url is not None:
                lines.append(f"Link: {wish.link_url}")
            budget.add_field(name=f"From {author_name}", value="\n".join(lines), inline=False)
        if overflow_count and index + 6 >= len(visible_wishes):
            budget.add_field(
                name="More unlocked wishes",
                value=f"+{overflow_count} more unlocked in `/birthday timeline`.",
                inline=False,
            )
        budget.set_footer(
            f"Bdayblaze Birthday Capsule | {announcement_theme_footer_label(announcement_theme)}"
        )
        embeds.append(budget.build())
    if not embeds:
        budget = BudgetedEmbed.create(
            title=f"{birthday_member.display_name}'s Birthday Capsule",
            description="No wishes were ready to unlock.",
            color=discord.Color(
                announcement_theme_color_value(
                    announcement_theme,
                    celebration_mode=celebration_mode,
                    accent_override=None,
                )
            ),
        )
        embeds.append(budget.build())
    return PreparedCapsuleReveal(
        content=birthday_member.mention,
        embeds=tuple(embeds[:2]),
    )


def batch_footer(theme: AnnouncementTheme, batch_token: str) -> str:
    return f"Bdayblaze {announcement_theme_footer_label(theme)} | {batch_token}"
