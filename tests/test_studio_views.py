from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bdayblaze.discord.ui.setup import (
    MembershipAgePresetSelect,
    MembershipIgnoreBotsSelect,
    MembershipMentionPresetSelect,
    MembershipRulesView,
    MessageTemplateView,
    QuestReactionTargetSelect,
    QuestSettingsView,
    QuestWishTargetSelect,
    ServerAnniversaryChannelSelect,
    ServerAnniversaryControlView,
    ServerAnniversaryDatePickerView,
    ServerAnniversaryDateSourceSelect,
    ServerAnniversaryDaySelect,
    ServerAnniversaryMonthSelect,
    SetupView,
    _build_studio_preview_pair,
    build_media_tools_embed,
    build_message_template_embed,
    build_setup_embed,
)
from bdayblaze.domain.models import (
    AnnouncementSurfaceSettings,
    GuildExperienceSettings,
    GuildSettings,
    RecurringCelebration,
)
from bdayblaze.services.media_validation_service import MediaProbeResult


class FakeSettingsService:
    async def describe_delivery(
        self,
        guild: object,
        *,
        kind: str,
        channel_id: int | None = None,
    ) -> object:
        from bdayblaze.domain.models import AnnouncementDeliveryReadiness

        return AnnouncementDeliveryReadiness(
            status="ready",
            summary=f"{kind} ready",
        )


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


def _surfaces() -> dict[str, AnnouncementSurfaceSettings]:
    return {
        "birthday_announcement": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="birthday_announcement",
            channel_id=123,
            image_url="https://cdn.example.com/current-banner.gif",
            thumbnail_url="https://cdn.example.com/current-thumb.webp",
        ),
        "anniversary": AnnouncementSurfaceSettings(
            guild_id=1,
            surface_kind="anniversary",
            channel_id=456,
        ),
    }


@pytest.mark.asyncio
async def test_message_template_view_configures_server_anniversary_buttons() -> None:
    view = MessageTemplateView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        announcement_surfaces=_surfaces(),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        section="server_anniversary",
        server_anniversary=_server_anniversary(),
        recurring_events=(),
    )

    assert view.edit_primary.label == "Schedule controls"
    assert view.edit_secondary.label == "Edit event copy"
    assert view.preview_current.label == "Preview selected surface"
    assert view.reset_current.label == "Reset to guild date"
    assert view.reset_media.label == "Server route/media"


@pytest.mark.asyncio
async def test_setup_view_removes_refresh_and_ignore_bots_buttons() -> None:
    view = SetupView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        announcement_surfaces=_surfaces(),
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


def test_message_template_embed_supports_reaction_quest_copy() -> None:
    embed = build_message_template_embed(
        GuildSettings.default(1),
        section="quests",
        guild=_guild(),  # type: ignore[arg-type]
        experience_settings=replace(
            GuildExperienceSettings.default(1),
            quests_enabled=True,
            quest_wish_target=4,
            quest_reaction_target=7,
        ),
    )

    values = "\n".join(field.value for field in embed.fields)
    assert "Reaction target: 7" in values
    assert "shared birthday announcement post" in values


def test_message_template_embed_home_summarizes_reaction_target() -> None:
    embed = build_message_template_embed(
        GuildSettings.default(1),
        section="home",
        guild=_guild(),  # type: ignore[arg-type]
        experience_settings=replace(
            GuildExperienceSettings.default(1),
            quests_enabled=True,
            quest_wish_target=4,
            quest_reaction_target=7,
        ),
    )

    quest_field = next(
        field for field in embed.fields if field.name == "\U0001F3AF Birthday Quests"
    )
    assert "Reaction target: 7" in quest_field.value


@pytest.mark.asyncio
async def test_server_anniversary_control_view_uses_native_controls() -> None:
    view = ServerAnniversaryControlView(
        settings_service=object(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        celebration=_server_anniversary(),
        recurring_events=(),
    )

    assert any(isinstance(child, ServerAnniversaryChannelSelect) for child in view.children)
    assert any(isinstance(child, ServerAnniversaryDateSourceSelect) for child in view.children)
    assert view.toggle_enabled.label == "Disable live"


@pytest.mark.asyncio
async def test_membership_rules_view_uses_native_select_controls() -> None:
    view = MembershipRulesView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
    )

    assert any(isinstance(child, MembershipIgnoreBotsSelect) for child in view.children)
    assert any(isinstance(child, MembershipAgePresetSelect) for child in view.children)
    assert any(isinstance(child, MembershipMentionPresetSelect) for child in view.children)


@pytest.mark.asyncio
async def test_quest_settings_view_uses_native_select_controls() -> None:
    view = QuestSettingsView(
        experience_service=object(),  # type: ignore[arg-type]
        settings=replace(
            GuildExperienceSettings.default(1),
            quests_enabled=True,
            quest_checkin_enabled=False,
        ),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
    )

    assert any(isinstance(child, QuestWishTargetSelect) for child in view.children)
    assert any(isinstance(child, QuestReactionTargetSelect) for child in view.children)
    assert view.toggle_enabled.label == "Disable live"
    assert view.toggle_checkin.label == "Enable check-in"


@pytest.mark.asyncio
async def test_server_anniversary_date_picker_view_uses_month_day_selects() -> None:
    view = ServerAnniversaryDatePickerView(
        settings_service=object(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        celebration=_server_anniversary(),
        recurring_events=(),
        selected_month=2,
        selected_day=29,
    )

    assert any(isinstance(child, ServerAnniversaryMonthSelect) for child in view.children)
    assert any(isinstance(child, ServerAnniversaryDaySelect) for child in view.children)


@pytest.mark.asyncio
async def test_birthday_dm_section_calls_out_theme_only_visuals() -> None:
    view = MessageTemplateView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=replace(GuildSettings.default(1), birthday_dm_enabled=True),
        announcement_surfaces=_surfaces(),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        section="birthday_dm",
        server_anniversary=_server_anniversary(),
        recurring_events=(),
    )
    embed = build_message_template_embed(
        replace(GuildSettings.default(1), birthday_dm_enabled=True),
        announcement_surfaces=_surfaces(),
        section="birthday_dm",
        guild=_guild(),  # type: ignore[arg-type]
    )

    values = "\n".join(field.value for field in embed.fields)
    assert view.edit_secondary.label == "Edit global look"
    assert "public announcement surfaces" in values


def test_media_tools_embed_explains_probe_results() -> None:
    embed = build_media_tools_embed(
        GuildSettings.default(1),
        announcement_surfaces=_surfaces(),
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
        surface_kind="birthday_announcement",
    )

    fields = {field.name: field.value for field in embed.fields}
    values = "\n".join(fields.values())

    assert fields["Updated"] == "No changes were saved. Your current saved media is unchanged."
    assert "Route: <#123> (custom)" in fields["Live surface"]
    assert "Image: direct GIF (custom)" in fields["Live surface"]
    assert "Thumbnail: direct image (custom)" in fields["Live surface"]
    assert "Blocked saves never clear the currently saved media." in fields["Save protection"]
    assert "Media source: custom" in fields["Inheritance and defaults"]
    assert (
        "Birthday announcement is the root default surface."
        in fields["Inheritance and defaults"]
    )
    assert "https://cdn.example.com/current-banner.gif" not in fields["Live surface"]
    assert (
        "https://tenor.com/view/funny-cat-happy-birthday-123456"
        not in fields["Live surface"]
    )
    assert "https://tenor.com/view/funny-cat-happy-birthday-123456" in fields["Latest validation"]
    assert "Webpage link rejected" in values
    assert "currently saved media" in fields["Save protection"]
    assert "For Tenor, use the direct media file URL, not the page link." in values
    assert "Google image results: wrapper links are webpages, not direct media files." in values


@pytest.mark.asyncio
async def test_message_template_view_shows_global_behavior_toggle() -> None:
    view = MessageTemplateView(
        settings_service=object(),  # type: ignore[arg-type]
        settings=GuildSettings.default(1),
        announcement_surfaces=_surfaces(),
        owner_id=42,
        guild=_guild(),  # type: ignore[arg-type]
        birthday_service=object(),  # type: ignore[arg-type]
        section="birthday",
        server_anniversary=_server_anniversary(),
        recurring_events=(),
    )

    labels = [getattr(child, "label", "") for child in view.children]

    assert "Style: Quiet" in labels


def test_setup_embed_shows_effective_source_for_inherited_surfaces() -> None:
    surfaces = _surfaces()
    surfaces.pop("anniversary")
    embed = build_setup_embed(
        GuildSettings.default(1),
        surfaces,
    )

    anniversary_field = next(
        field for field in embed.fields if field.name == "\U0001F389 Member anniversaries"
    )
    server_field = next(
        field for field in embed.fields if field.name == "\U0001F4C5 Annual celebrations"
    )

    assert "Route: <#123> (inherits birthday default)" in anniversary_field.value
    assert "Image: direct GIF (inherits birthday default)" in anniversary_field.value
    assert "Thumbnail: direct image (inherits birthday default)" in anniversary_field.value
    assert "Saved event-level channel overrides still win" in server_field.value


def test_message_template_embed_help_calls_out_anniversary_placeholder_validity() -> None:
    embed = build_message_template_embed(
        GuildSettings.default(1),
        section="help",
        guild=_guild(),  # type: ignore[arg-type]
    )

    placeholder_field = next(
        field for field in embed.fields if field.name == "\U0001F389 Anniversary placeholder rules"
    )

    assert "{anniversary.years}" in placeholder_field.value
    assert "Member anniversary only" in placeholder_field.value
    assert "{server_anniversary.years_since_creation}" in placeholder_field.value
    assert "Server anniversary only" in placeholder_field.value


@pytest.mark.asyncio
async def test_studio_preview_uses_explicit_surface_selection() -> None:
    settings = replace(
        GuildSettings.default(1),
        announcements_enabled=True,
        anniversary_enabled=True,
        announcement_template="Happy birthday {birthday.mentions}",
        anniversary_template="Happy anniversary {members.names}",
        birthday_dm_enabled=True,
    )

    status_embed, preview_embed = await _build_studio_preview_pair(
        guild=_guild(),  # type: ignore[arg-type]
        settings=settings,
        settings_service=FakeSettingsService(),  # type: ignore[arg-type]
        section="birthday",
        announcement_surfaces=_surfaces(),
        preview_kind="anniversary",
        server_anniversary=_server_anniversary(),
        recurring_events=(),
    )

    status_fields = {field.name: field.value for field in status_embed.fields}

    assert status_fields["Preview surface"] == "Member anniversary"
    assert "Route: <#456> (custom)" in status_fields["Routing and mentions"]
    assert "Route source: custom" in status_fields["Routing and mentions"]
    assert "Image: direct GIF (inherits birthday default)" in status_fields["Media and visuals"]
    assert (
        "Thumbnail: direct image (inherits birthday default)"
        in status_fields["Media and visuals"]
    )
    assert "Happy anniversary" in (preview_embed.description or "")
