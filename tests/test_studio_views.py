from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from bdayblaze.discord.ui.setup import (
    MessageTemplateView,
    ServerAnniversaryChannelSelect,
    ServerAnniversaryControlView,
    ServerAnniversaryDateSourceSelect,
    SetupView,
    build_media_tools_embed,
    build_message_template_embed,
)
from bdayblaze.domain.models import GuildExperienceSettings, GuildSettings, RecurringCelebration
from bdayblaze.services.media_validation_service import MediaProbeResult


def _guild() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        name="Birthday Club",
        created_at=datetime(2020, 3, 25, tzinfo=UTC),
    )


def _server_anniversary() -> RecurringCelebration:
    return RecurringCelebration(
        id=1,
        guild_id=1,
        name="Server anniversary",
        event_month=3,
        event_day=25,
        channel_id=123,
        template="Today we celebrate {event.name}",
        enabled=True,
        next_occurrence_at_utc=datetime(2027, 3, 25, tzinfo=UTC),
        celebration_kind="server_anniversary",
        use_guild_created_date=False,
    )


def test_message_template_view_configures_server_anniversary_buttons() -> None:
    view = MessageTemplateView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=replace(GuildSettings.default(1), announcement_channel_id=123),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        section="server_anniversary",
        server_anniversary=_server_anniversary(),
        recurring_events=(),
    )

    assert view.edit_primary.label == "Schedule controls"
    assert view.edit_secondary.label == "Edit event copy"
    assert view.preview_current.label == "Preview server anniversary"
    assert view.reset_current.label == "Reset to guild date"
    assert view.reset_media.label == "Media tools"


def test_setup_view_removes_refresh_and_ignore_bots_buttons() -> None:
    view = SetupView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
    )

    labels = [getattr(child, "label", "") for child in view.children]
    assert "Refresh" not in labels
    assert "Toggle ignore bots" not in labels


def test_message_template_embed_supports_capsules_section() -> None:
    embed = build_message_template_embed(
        GuildSettings.default(1),
        section="capsules",
        guild=_guild(),  # type: ignore[arg-type]
        experience_settings=GuildExperienceSettings.default(1),
    )

    values = "\n".join(field.value for field in embed.fields)
    assert "One queued wish per author-to-target pair" in values


def test_server_anniversary_control_view_uses_native_controls() -> None:
    view = ServerAnniversaryControlView(
        settings_service=object(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        settings=replace(GuildSettings.default(1), announcement_channel_id=123),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        celebration=_server_anniversary(),
        recurring_events=(),
    )

    assert any(isinstance(child, ServerAnniversaryChannelSelect) for child in view.children)
    assert any(isinstance(child, ServerAnniversaryDateSourceSelect) for child in view.children)
    assert view.toggle_enabled.label == "Disable live"


def test_birthday_dm_section_calls_out_theme_only_visuals() -> None:
    view = MessageTemplateView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=replace(
            GuildSettings.default(1),
            birthday_dm_enabled=True,
            announcement_channel_id=123,
        ),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        section="birthday_dm",
        server_anniversary=_server_anniversary(),
        recurring_events=(),
    )
    embed = build_message_template_embed(
        replace(
            GuildSettings.default(1),
            birthday_dm_enabled=True,
            announcement_channel_id=123,
        ),
        section="birthday_dm",
        guild=_guild(),  # type: ignore[arg-type]
    )

    values = "\n".join(field.value for field in embed.fields)
    assert view.edit_secondary.label == "Edit announcement visuals"
    assert "public announcement surfaces" in values


def test_media_tools_embed_explains_probe_results() -> None:
    embed = build_media_tools_embed(
        replace(
            GuildSettings.default(1),
            announcement_image_url="https://cdn.example.com/current-banner.gif",
            announcement_thumbnail_url="https://cdn.example.com/current-thumb.webp",
        ),
        note="No changes were saved. Your current saved media is unchanged.",
        image_probe=MediaProbeResult(
            label="Announcement image",
            url="https://tenor.com/view/funny-cat-happy-birthday-123456",
            classification="webpage",
            summary=(
                "Announcement image URL looks like a webpage, not a direct GIF/image file. "
                "For Tenor, use the direct media file URL, not the page link."
            ),
            direct_render_expected=False,
            content_type="text/html",
            detected_kind="html",
        ),
        thumbnail_probe=MediaProbeResult(
            label="Announcement thumbnail",
            url=(
                "https://www.google.com/imgres?imgurl=https%3A%2F%2Fcdn.example.com%2Fparty.gif"
                "&imgrefurl=https%3A%2F%2Fexample.com%2Fpost"
            ),
            classification="webpage",
            summary=(
                "Announcement thumbnail URL looks like a webpage, not a direct GIF/image file. "
                "Google image-result links are wrappers, not direct media files. "
                "Try copying the image/GIF address itself, not the browser page URL."
            ),
            direct_render_expected=False,
            content_type="text/html",
            detected_kind="html",
        ),
        checked_image_url="https://tenor.com/view/funny-cat-happy-birthday-123456",
        checked_thumbnail_url=(
            "https://www.google.com/imgres?imgurl=https%3A%2F%2Fcdn.example.com%2Fparty.gif"
            "&imgrefurl=https%3A%2F%2Fexample.com%2Fpost"
        ),
    )

    fields = {field.name: field.value for field in embed.fields}
    values = "\n".join(fields.values())

    assert fields["Updated"] == "No changes were saved. Your current saved media is unchanged."
    assert "https://cdn.example.com/current-banner.gif" in fields["Current saved media"]
    assert "https://tenor.com/view/funny-cat-happy-birthday-123456" not in fields["Current saved media"]
    assert "https://tenor.com/view/funny-cat-happy-birthday-123456" in fields["Latest validation"]
    assert "Webpage link rejected" in values
    assert "Direct media accepted" in values
    assert "For Tenor, use the direct media file URL, not the page link." in values
    assert "Google image results: wrapper links are webpages, not direct media files." in values
