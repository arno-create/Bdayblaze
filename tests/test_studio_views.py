from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from bdayblaze.discord.ui.setup import (
    MessageTemplateView,
    ServerAnniversaryChannelSelect,
    ServerAnniversaryControlView,
    ServerAnniversaryDateSourceSelect,
    build_media_tools_embed,
    build_message_template_embed,
)
from bdayblaze.domain.models import GuildSettings, RecurringCelebration
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
        GuildSettings.default(1),
        note="No changes were saved.",
        image_probe=MediaProbeResult(
            label="Announcement image",
            url="https://cdn.example.com/banner.gif?sig=abc",
            classification="direct_media",
            summary="Announcement image URL responded as direct media.",
            direct_render_expected=True,
            content_type="image/gif",
            detected_kind="gif",
        ),
        thumbnail_probe=MediaProbeResult(
            label="Announcement thumbnail",
            url="https://www.example.com/gallery/photo-42",
            classification="webpage",
            summary="Announcement thumbnail URL responded as a webpage.",
            direct_render_expected=False,
            content_type="text/html",
            detected_kind="html",
        ),
    )

    values = "\n".join(field.value for field in embed.fields)
    assert "Likely direct media" in values
    assert "Webpage URL" in values
    assert "Discord will not render" in values
