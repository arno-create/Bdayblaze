from __future__ import annotations

from bdayblaze.discord.announcements import build_announcement_message
from bdayblaze.domain.announcement_template import AnnouncementRenderRecipient
from bdayblaze.domain.models import AnnouncementStudioPresentation


def _recipient(name: str, timezone: str) -> AnnouncementRenderRecipient:
    return AnnouncementRenderRecipient(
        mention=f"@{name}",
        display_name=name,
        username=name.lower(),
        birth_month=3,
        birth_day=24,
        timezone=timezone,
    )


def _presentation(**kwargs: object) -> AnnouncementStudioPresentation:
    defaults = {
        "theme": "festive",
        "title_override": None,
        "footer_text": None,
        "image_url": None,
        "thumbnail_url": None,
        "accent_color": None,
    }
    defaults.update(kwargs)
    return AnnouncementStudioPresentation(**defaults)  # type: ignore[arg-type]


def test_build_announcement_message_uses_theme_footer_for_live_posts() -> None:
    prepared = build_announcement_message(
        kind="birthday_announcement",
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC")],
        celebration_mode="party",
        announcement_theme="festive",
        presentation=_presentation(),
        template="Happy birthday {birthday.mentions}",
        batch_token="announcement-batch:1",
    )

    assert prepared.content == "@Jamie"
    assert prepared.embed.footer.text == "Bdayblaze Festive | announcement-batch:1"


def test_build_announcement_message_applies_studio_lite_fields() -> None:
    prepared = build_announcement_message(
        kind="birthday_announcement",
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC")],
        celebration_mode="quiet",
        announcement_theme="minimal",
        presentation=_presentation(
            theme="minimal",
            title_override="Birthday Bulletin",
            footer_text="Powered by Bdayblaze",
            image_url="https://cdn.example.com/banner.gif",
            thumbnail_url="https://cdn.example.com/avatar.png",
            accent_color=0x123456,
        ),
        template="Happy birthday {birthday.names}",
        batch_token="announcement-batch:1",
    )

    assert prepared.embed.title == "Birthday Bulletin"
    assert prepared.embed.footer.text == (
        "Powered by Bdayblaze | Bdayblaze Minimal | announcement-batch:1"
    )
    assert prepared.embed.image.url == "https://cdn.example.com/banner.gif"
    assert prepared.embed.thumbnail.url == "https://cdn.example.com/avatar.png"
    assert prepared.embed.color.value == 0x123456


def test_build_announcement_message_marks_preview_embeds() -> None:
    prepared = build_announcement_message(
        kind="birthday_announcement",
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC"), _recipient("Rin", "Europe/Berlin")],
        celebration_mode="quiet",
        announcement_theme="minimal",
        presentation=_presentation(theme="minimal"),
        template="Happy birthday {birthday.names}",
        preview_label="Preview only - batch example",
    )

    assert prepared.embed.author.name == "Preview only - batch example"
    assert prepared.embed.footer.text == "Bdayblaze Preview | Minimal"


def test_build_announcement_message_suppresses_mentions_for_large_batches() -> None:
    prepared = build_announcement_message(
        kind="birthday_announcement",
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC"), _recipient("Rin", "UTC")],
        celebration_mode="party",
        announcement_theme="classic",
        presentation=_presentation(theme="classic"),
        template="Happy birthday {birthday.names}",
        mention_suppressed=True,
    )

    assert prepared.content == ""


def test_build_announcement_message_prefixes_late_delivery_note() -> None:
    prepared = build_announcement_message(
        kind="birthday_announcement",
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC")],
        celebration_mode="quiet",
        announcement_theme="classic",
        presentation=_presentation(theme="classic"),
        template="Happy birthday {birthday.names}",
        late_delivery=True,
    )

    assert prepared.embed.description.startswith(
        "We missed the exact moment, but not the celebration."
    )
