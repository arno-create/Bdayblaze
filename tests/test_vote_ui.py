from __future__ import annotations

from datetime import UTC, datetime

import discord

from bdayblaze.discord.ui.vote import (
    build_owner_vote_status_text,
    build_vote_embed,
    build_vote_reminder_embed,
    build_vote_view,
)
from bdayblaze.domain.topgg import VoteBonusStatus


def _status(
    *,
    lane_state: str,
    active: bool,
    refresh_available: bool,
    refresh_retry_after_seconds: int | None = None,
    reminders_enabled: bool = False,
    reminder_lane_state: str = "off",
    next_reminder_at_utc: datetime | None = None,
    last_reminder_error_code: str | None = None,
    reminder_timing_source: str | None = None,
) -> VoteBonusStatus:
    return VoteBonusStatus(
        lane_state=lane_state,
        enabled=lane_state != "disabled",
        active=active,
        configuration_message=None,
        voted_at_utc=datetime(2026, 4, 24, 12, tzinfo=UTC) if active else None,
        expires_at_utc=datetime(2026, 4, 25, 0, tzinfo=UTC) if active else None,
        timing_source="exact" if lane_state == "active_exact" else None,
        weight=1 if active else None,
        refresh_available=refresh_available,
        refresh_cooldown_seconds=60,
        refresh_retry_after_seconds=refresh_retry_after_seconds,
        wish_character_limit=500 if active else 350,
        timeline_entry_limit=12 if active else 6,
        reminders_enabled=reminders_enabled,
        reminder_lane_state=reminder_lane_state,
        next_reminder_at_utc=next_reminder_at_utc,
        last_reminder_error_code=last_reminder_error_code,
        reminder_timing_source=reminder_timing_source,
    )


def test_build_vote_embed_uses_calm_disabled_copy_without_premium_language() -> None:
    embed = build_vote_embed(_status(lane_state="disabled", active=False, refresh_available=False))

    assert "Top.gg vote bonus" in (embed.title or "")
    assert "disabled" in (embed.description or "").lower()
    assert "premium" not in (embed.description or "").lower()


def test_build_vote_view_shows_refresh_button_only_when_available() -> None:
    active_view = build_vote_view(
        _status(
            lane_state="active_exact",
            active=True,
            refresh_available=True,
            reminders_enabled=True,
            reminder_lane_state="armed_exact",
        )
    )
    inactive_view = build_vote_view(
        _status(
            lane_state="inactive",
            active=False,
            refresh_available=False,
            reminders_enabled=False,
            reminder_lane_state="off",
        )
    )

    active_buttons = [
        child.label
        for child in active_view.children
        if isinstance(child, discord.ui.Button)
    ]
    inactive_buttons = [
        child.label
        for child in inactive_view.children
        if isinstance(child, discord.ui.Button)
    ]

    assert "Vote on Top.gg" in active_buttons
    assert "Refresh" in active_buttons
    assert "Disable reminders" in active_buttons
    assert inactive_buttons == ["Vote on Top.gg", "Enable reminders"]


def test_build_vote_embed_reports_armed_reminder_state() -> None:
    embed = build_vote_embed(
        _status(
            lane_state="active_exact",
            active=True,
            refresh_available=False,
            reminders_enabled=True,
            reminder_lane_state="armed_exact",
            next_reminder_at_utc=datetime(2026, 4, 24, 23, 30, tzinfo=UTC),
            reminder_timing_source="exact",
        )
    )

    reminder_field = next(field for field in embed.fields if field.name == "Reminder status")
    assert "armed" in reminder_field.value.lower()
    assert "23" in reminder_field.value or "relative" or reminder_field.value


def test_build_owner_vote_status_text_stays_private_and_truthful() -> None:
    text = build_owner_vote_status_text(
        diagnostics={
            "configuration_state": "ready",
            "webhook_mode": "v2",
            "refresh_available": True,
            "storage_backend": "postgres",
        },
        status=_status(lane_state="active_exact", active=True, refresh_available=True),
    )

    assert "Top.gg diagnostics" in text
    assert "premium" not in text.lower()
    assert "active_exact" in text


def test_build_vote_reminder_embed_sets_vote_url_footer() -> None:
    embed = build_vote_reminder_embed(
        _status(lane_state="active_exact", active=True, refresh_available=False),
        vote_url="https://top.gg/bot/1485920716573380660/vote",
    )

    assert embed.footer.text == "https://top.gg/bot/1485920716573380660/vote"
