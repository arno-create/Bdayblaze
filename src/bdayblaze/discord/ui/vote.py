from __future__ import annotations

import asyncio

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed
from bdayblaze.domain.topgg import VoteBonusStatus

TOPGG_VOTE_URL = "https://top.gg/bot/1485920716573380660/vote"


class _DetachedVoteView:
    def __init__(self) -> None:
        self.children: list[discord.ui.Item[object]] = []

    def add_item(self, item: discord.ui.Item[object]) -> None:
        self.children.append(item)


def build_vote_embed(
    status: VoteBonusStatus,
    *,
    notice: str | None = None,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Top.gg vote bonus",
        description=_description_for_status(status),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="While active",
        value=(
            "Birthday Capsule wishes can use up to 500 characters.\n"
            "Your private birthday timeline can show up to 12 celebrations."
        ),
        inline=False,
    )
    budget.add_field(
        name="Current limits",
        value=(
            f"Wish text: {status.wish_character_limit} characters\n"
            f"Private timeline history: {status.timeline_entry_limit} celebrations"
        ),
        inline=False,
    )
    if not status.refresh_available and status.lane_state not in {"disabled", "misconfigured"}:
        budget.add_field(
            name="Refresh availability",
            value="Manual refresh is unavailable on this deployment right now.",
            inline=False,
        )
    if status.expires_at_utc is not None and status.active:
        budget.add_field(
            name="Window",
            value=(
                f"Ends {discord.utils.format_dt(status.expires_at_utc, 'R')}.\n"
                + (
                    "Timing: estimated from legacy Top.gg delivery."
                    if status.timing_source == "legacy_estimated"
                    else "Timing: exact from Top.gg."
                )
            ),
            inline=False,
        )
    if status.configuration_message and status.lane_state in {"disabled", "misconfigured"}:
        budget.add_field(
            name="Availability",
            value=status.configuration_message,
            inline=False,
        )
    if notice:
        budget.add_field(name="Refresh", value=notice, inline=False)
    return budget.build()


def build_vote_view(
    status: VoteBonusStatus,
    *,
    vote_url: str = TOPGG_VOTE_URL,
) -> discord.ui.View | _DetachedVoteView:
    try:
        asyncio.get_running_loop()
        view: discord.ui.View | _DetachedVoteView = discord.ui.View(timeout=None)
    except RuntimeError:
        view = _DetachedVoteView()
    view.add_item(
        discord.ui.Button(
            label="Vote on Top.gg",
            style=discord.ButtonStyle.link,
            url=vote_url,
        )
    )
    if status.refresh_available:
        label = (
            f"Refresh ({status.refresh_retry_after_seconds}s)"
            if status.refresh_retry_after_seconds is not None
            else "Refresh"
        )
        view.add_item(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id="topgg-refresh",
                disabled=status.refresh_retry_after_seconds is not None,
            )
        )
    return view


def build_owner_vote_status_text(
    *,
    diagnostics: dict[str, object],
    status: VoteBonusStatus | None,
    discord_user_id: int | None = None,
    receipts: list[object] | None = None,
) -> str:
    lines = [
        "Top.gg diagnostics",
        f"Configuration state: {diagnostics.get('configuration_state')}",
        f"Webhook mode: {diagnostics.get('webhook_mode')}",
        f"Refresh available: {diagnostics.get('refresh_available')}",
        f"Storage backend: {diagnostics.get('storage_backend')}",
    ]
    if discord_user_id is not None:
        lines.append("")
        lines.append(f"User: {discord_user_id}")
    if status is not None:
        lines.extend(
            [
                f"Lane state: {status.lane_state}",
                f"Timing source: {status.timing_source}",
                f"Wish character limit: {status.wish_character_limit}",
                f"Timeline entry limit: {status.timeline_entry_limit}",
            ]
        )
        if status.expires_at_utc is not None:
            lines.append(f"Vote expires: {status.expires_at_utc.isoformat()}")
    if receipts:
        lines.append(f"Recent receipts: {len(receipts)}")
    return "\n".join(lines)


def _description_for_status(status: VoteBonusStatus) -> str:
    if status.lane_state == "disabled":
        return "Top.gg vote bonus is disabled for this deployment."
    if status.lane_state == "misconfigured":
        return "Top.gg vote bonus is temporarily unavailable right now."
    if status.lane_state == "active_exact":
        return "Your Top.gg vote bonus is active right now."
    if status.lane_state == "active_estimated":
        return "Your Top.gg vote bonus is active right now with estimated timing."
    return "No active vote bonus right now. Vote on Top.gg to unlock a temporary boost."
