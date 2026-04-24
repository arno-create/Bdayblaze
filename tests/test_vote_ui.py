from __future__ import annotations

from datetime import UTC, datetime

import discord

from bdayblaze.discord.ui.vote import (
    build_owner_vote_status_text,
    build_vote_embed,
    build_vote_view,
)
from bdayblaze.domain.topgg import VoteBonusStatus


def _status(
    *,
    lane_state: str,
    active: bool,
    refresh_available: bool,
    refresh_retry_after_seconds: int | None = None,
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
    )


def test_build_vote_embed_uses_calm_disabled_copy_without_premium_language() -> None:
    embed = build_vote_embed(_status(lane_state="disabled", active=False, refresh_available=False))

    assert "Top.gg vote bonus" in (embed.title or "")
    assert "disabled" in (embed.description or "").lower()
    assert "premium" not in (embed.description or "").lower()


def test_build_vote_view_shows_refresh_button_only_when_available() -> None:
    active_view = build_vote_view(_status(lane_state="active_exact", active=True, refresh_available=True))
    inactive_view = build_vote_view(_status(lane_state="inactive", active=False, refresh_available=False))

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
    assert inactive_buttons == ["Vote on Top.gg"]


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
