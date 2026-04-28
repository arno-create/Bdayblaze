from __future__ import annotations

import asyncio
from collections.abc import Sequence

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed
from bdayblaze.domain.topgg import VoteBonusStatus

TOPGG_VOTE_URL = "https://top.gg/bot/1485920716573380660/vote"
_VOTE_EMBED_COLOR = discord.Color.from_rgb(199, 102, 45)


class _DetachedVoteView:
    def __init__(self) -> None:
        self.children: list[discord.ui.Item[discord.ui.View]] = []

    def add_item(self, item: discord.ui.Item[discord.ui.View]) -> None:
        self.children.append(item)


def build_vote_embed(
    status: VoteBonusStatus,
    *,
    notice: str | None = None,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Top.gg vote bonus",
        description=_description_for_status(status),
        color=_VOTE_EMBED_COLOR,
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
    budget.add_field(
        name="Reminder status",
        value=_reminder_status_text(status),
        inline=False,
    )
    if not status.refresh_available and status.lane_state not in {"disabled", "misconfigured"}:
        budget.add_field(
            name="Refresh availability",
            value=(
                "Webhooks are active. "
                "Optional manual refresh is not configured on this deployment."
            ),
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
        budget.add_field(name="Update", value=notice, inline=False)
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
    view.add_item(
        discord.ui.Button(
            label="Disable reminders" if status.reminders_enabled else "Enable reminders",
            style=discord.ButtonStyle.secondary,
            custom_id="topgg-reminders",
        )
    )
    return view


def build_owner_vote_status_text(
    *,
    diagnostics: dict[str, object],
    status: VoteBonusStatus | None,
    discord_user_id: int | None = None,
    receipts: Sequence[object] | None = None,
) -> str:
    lines = [
        "Top.gg diagnostics",
        f"Configuration state: {diagnostics.get('configuration_state')}",
        f"Webhook mode: {diagnostics.get('webhook_mode')}",
        f"Refresh available: {diagnostics.get('refresh_available')}",
        f"Storage backend: {diagnostics.get('storage_backend')}",
    ]
    storage_message = diagnostics.get("storage_message")
    if storage_message:
        lines.append(f"Storage message: {storage_message}")
    reminder_ready = diagnostics.get("reminder_ready")
    if reminder_ready is not None:
        lines.append(f"Reminder readiness: {reminder_ready}")
    reminder_delivery_mode = diagnostics.get("reminder_delivery_mode")
    if reminder_delivery_mode:
        lines.append(f"Reminder delivery mode: {reminder_delivery_mode}")
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
                f"Reminders enabled: {status.reminders_enabled}",
                f"Reminder lane state: {status.reminder_lane_state}",
            ]
        )
        if status.expires_at_utc is not None:
            lines.append(f"Vote expires: {status.expires_at_utc.isoformat()}")
        if status.next_reminder_at_utc is not None:
            lines.append(f"Next reminder: {status.next_reminder_at_utc.isoformat()}")
        if status.last_reminder_error_code:
            lines.append(f"Last reminder error: {status.last_reminder_error_code}")
    if receipts:
        lines.append(f"Recent receipts: {len(receipts)}")
    return "\n".join(lines)


def build_vote_reminder_embed(
    status: VoteBonusStatus,
    *,
    vote_url: str = TOPGG_VOTE_URL,
) -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="Your Top.gg vote boost is nearing its end",
        description=(
            "Vote again on Top.gg to keep the temporary utility boost active."
            if status.active
            else "Vote on Top.gg to restore the temporary utility boost."
        ),
        color=_VOTE_EMBED_COLOR,
    )
    budget.add_field(
        name="Current temporary limits",
        value=(
            f"Wish text: {status.wish_character_limit} characters\n"
            f"Private timeline history: {status.timeline_entry_limit} celebrations"
        ),
        inline=False,
    )
    if status.expires_at_utc is not None:
        budget.add_field(
            name="Vote window",
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
    budget.set_footer(vote_url)
    return budget.build()


def _description_for_status(status: VoteBonusStatus) -> str:
    if status.lane_state == "disabled":
        return "Top.gg vote bonus is intentionally disabled for this deployment."
    if status.lane_state == "misconfigured":
        return "Top.gg vote bonus is temporarily unavailable right now."
    if status.lane_state == "active_exact":
        return "Your Top.gg vote bonus is active right now."
    if status.lane_state == "active_estimated":
        return "Your Top.gg vote bonus is active right now with estimated timing."
    return "No active vote bonus right now. Vote on Top.gg to unlock a temporary boost."


def _reminder_status_text(status: VoteBonusStatus) -> str:
    if not status.reminders_enabled:
        return "Off. Enable reminders if you want a quiet DM before the vote window ends."
    if status.reminder_lane_state == "armed_exact" and status.next_reminder_at_utc is not None:
        return (
            "Armed for this exact vote window.\n"
            f"Next reminder: {discord.utils.format_dt(status.next_reminder_at_utc, 'R')}."
        )
    if status.reminder_lane_state == "armed_estimated" and status.next_reminder_at_utc is not None:
        return (
            "Armed for this estimated vote window.\n"
            f"Next reminder: {discord.utils.format_dt(status.next_reminder_at_utc, 'R')}."
        )
    if status.reminder_lane_state == "delivery_issue":
        issue = status.last_reminder_error_code or "delivery_issue"
        return f"A recent reminder could not be delivered. Last issue: `{issue}`."
    return "On. Waiting for your next valid vote window before arming a reminder."
