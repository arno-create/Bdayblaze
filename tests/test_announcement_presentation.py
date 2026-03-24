from __future__ import annotations

from bdayblaze.discord.announcements import build_announcement_message
from bdayblaze.domain.announcement_template import AnnouncementRenderRecipient


def _recipient(name: str, timezone: str) -> AnnouncementRenderRecipient:
    return AnnouncementRenderRecipient(
        mention=f"@{name}",
        display_name=name,
        username=name.lower(),
        birth_month=3,
        birth_day=24,
        timezone=timezone,
    )


def test_build_announcement_message_uses_theme_footer_for_live_posts() -> None:
    prepared = build_announcement_message(
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC")],
        celebration_mode="party",
        announcement_theme="festive",
        template="Happy birthday {birthday.mentions}",
        batch_token="announcement-batch:1",
    )

    assert prepared.content == "@Jamie"
    assert prepared.embed.title.startswith("🎉")
    assert prepared.embed.footer.text == "Bdayblaze Festive | announcement-batch:1"


def test_build_announcement_message_marks_preview_embeds() -> None:
    prepared = build_announcement_message(
        server_name="Birthday Club",
        recipients=[_recipient("Jamie", "UTC"), _recipient("Rin", "Europe/Berlin")],
        celebration_mode="quiet",
        announcement_theme="minimal",
        template="Happy birthday {birthday.names}",
        preview_label="Preview only - batch example",
    )

    assert prepared.embed.author.name == "Preview only - batch example"
    assert prepared.embed.footer.text == "Bdayblaze Preview | Minimal"
