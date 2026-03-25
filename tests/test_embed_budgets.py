from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import discord

from bdayblaze.discord.cogs.birthday import (
    _build_dry_run_status_embed,
    _build_health_embed,
    _build_import_preview_embed,
    _build_privacy_embed,
    _build_recurring_event_list_embed,
)
from bdayblaze.discord.embed_budget import (
    EMBED_AUTHOR_LIMIT,
    EMBED_DESCRIPTION_LIMIT,
    EMBED_FIELD_LIMIT,
    EMBED_FIELD_NAME_LIMIT,
    EMBED_FIELD_VALUE_LIMIT,
    EMBED_FOOTER_LIMIT,
    EMBED_TITLE_LIMIT,
    EMBED_TOTAL_LIMIT,
    embed_text_length,
)
from bdayblaze.discord.ui import info
from bdayblaze.discord.ui.setup import (
    build_message_template_embed,
    build_server_anniversary_control_embed,
    build_setup_embed,
)
from bdayblaze.domain.models import (
    AnnouncementDeliveryReadiness,
    BirthdayImportError,
    BirthdayImportPreview,
    GuildSettings,
    HealthIssue,
    RecurringCelebration,
)


def _assert_embed_within_limits(embed: discord.Embed) -> None:
    assert len(embed.title or "") <= EMBED_TITLE_LIMIT
    assert len(embed.description or "") <= EMBED_DESCRIPTION_LIMIT
    assert len(embed.footer.text or "") <= EMBED_FOOTER_LIMIT
    author_name = embed.author.name if embed.author else ""
    assert len(author_name or "") <= EMBED_AUTHOR_LIMIT
    assert len(embed.fields) <= EMBED_FIELD_LIMIT
    assert embed_text_length(embed) <= EMBED_TOTAL_LIMIT
    for field in embed.fields:
        assert len(field.name) <= EMBED_FIELD_NAME_LIMIT
        assert len(field.value) <= EMBED_FIELD_VALUE_LIMIT


def _settings() -> GuildSettings:
    return replace(
        GuildSettings.default(1),
        announcements_enabled=True,
        birthday_dm_enabled=True,
        anniversary_enabled=True,
        announcement_channel_id=123,
        anniversary_channel_id=456,
        announcement_template="A" * 1200,
        birthday_dm_template="B" * 1200,
        anniversary_template="C" * 1200,
        announcement_title_override="T" * 256,
        announcement_footer_text="F" * 512,
        announcement_image_url=_long_url(".gif"),
        announcement_thumbnail_url=_long_url(".png"),
        announcement_accent_color=0xABCDEF,
    )


def _long_url(suffix: str) -> str:
    prefix = "https://cdn.example.com/"
    remaining = 500 - len(prefix) - len(suffix)
    return prefix + ("a" * remaining) + suffix


def _server_anniversary() -> RecurringCelebration:
    return RecurringCelebration(
        id=91,
        guild_id=1,
        name="Server anniversary",
        event_month=3,
        event_day=25,
        channel_id=999,
        template="S" * 1200,
        enabled=True,
        next_occurrence_at_utc=datetime(2027, 3, 25, tzinfo=UTC),
        celebration_kind="server_anniversary",
        use_guild_created_date=False,
    )


def _recurring_events() -> tuple[RecurringCelebration, ...]:
    return tuple(
        RecurringCelebration(
            id=index,
            guild_id=1,
            name=f"Event {index} " + ("x" * 60),
            event_month=3,
            event_day=min(28, index),
            channel_id=200 + index,
            template="Event {event.name}",
            enabled=index % 2 == 0,
            next_occurrence_at_utc=datetime(2027, 3, min(28, index), tzinfo=UTC),
        )
        for index in range(1, 9)
    )


def test_studio_and_setup_embeds_stay_within_discord_limits() -> None:
    settings = _settings()
    guild = SimpleNamespace(created_at=datetime(2020, 3, 25, tzinfo=UTC))
    server_anniversary = _server_anniversary()
    recurring_events = _recurring_events()

    embeds = [
        build_setup_embed(settings, note="Saved."),
        build_message_template_embed(settings, section="home", guild=guild),
        build_message_template_embed(
            settings,
            section="birthday",
            guild=guild,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        ),
        build_message_template_embed(
            settings,
            section="birthday_dm",
            guild=guild,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        ),
        build_message_template_embed(
            settings,
            section="anniversary",
            guild=guild,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        ),
        build_message_template_embed(
            settings,
            section="server_anniversary",
            guild=guild,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        ),
        build_message_template_embed(
            settings,
            section="events",
            guild=guild,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        ),
        build_message_template_embed(
            settings,
            section="help",
            guild=guild,
            server_anniversary=server_anniversary,
            recurring_events=recurring_events,
        ),
    ]

    for embed in embeds:
        _assert_embed_within_limits(embed)


def test_admin_status_and_info_embeds_stay_within_discord_limits() -> None:
    readiness = AnnouncementDeliveryReadiness(
        status="blocked",
        summary="Preview ready. Live delivery is blocked.",
        details=tuple(f"Detail {index}: " + ("x" * 130) for index in range(12)),
    )
    issues = [
        HealthIssue(
            severity="warning",
            code=f"issue_{index}",
            summary="Summary " + ("x" * 120),
            action="Action " + ("y" * 80),
        )
        for index in range(12)
    ]
    preview = BirthdayImportPreview(
        total_rows=15,
        valid_rows=(),
        errors=tuple(
            BirthdayImportError(
                row_number=index + 2,
                message="Bad row " + ("z" * 140),
            )
            for index in range(12)
        ),
        apply_token="token-123",
    )

    embeds = [
        info.build_help_embed(),
        info.build_about_embed(),
        _build_dry_run_status_embed(
            readiness,
            _settings(),
            kind="birthday_announcement",
            channel_id=123,
            preview_member_count=2,
        ),
        _build_health_embed(issues),
        _build_import_preview_embed(preview),
        _build_import_preview_embed(preview, applied=True),
        _build_privacy_embed(),
        _build_recurring_event_list_embed(list(_recurring_events())),
        build_server_anniversary_control_embed(
            _settings(),
            guild=SimpleNamespace(created_at=datetime(2020, 3, 25, tzinfo=UTC)),
            celebration=_server_anniversary(),
        ),
    ]

    for embed in embeds:
        _assert_embed_within_limits(embed)


def test_help_section_splits_placeholder_reference_into_multiple_fields() -> None:
    embed = build_message_template_embed(_settings(), section="help")

    assert len(embed.fields) >= 4
    _assert_embed_within_limits(embed)
