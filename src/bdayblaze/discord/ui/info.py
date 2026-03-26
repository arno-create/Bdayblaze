from __future__ import annotations

from importlib.metadata import PackageNotFoundError, metadata, version
from typing import cast

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed


def build_help_embed() -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="\u2753 Bdayblaze Help",
        description=(
            "Birthday tracking for Discord servers with privacy-first defaults and "
            "lightweight admin controls."
        ),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="Getting started",
        value=(
            "1. Save your date with `/birthday set`.\n"
            "2. Server admins should open `/birthday studio`.\n"
            "3. Use `/birthday setup` for routing, timezone, and safety rules.\n"
            "4. Use `/birthday test-message` before going live."
        ),
        inline=False,
    )
    budget.add_field(
        name="User commands",
        value=(
            "`/birthday set` Save your birthday for this server.\n"
            "`/birthday view` See your saved record.\n"
            "`/birthday remove` Delete your server-scoped data.\n"
            "`/birthday privacy` Review visibility and privacy defaults.\n"
            "`/birthday today` Show birthdays currently active under bot celebration logic.\n"
            "`/birthday next` See the nearest upcoming birthday.\n"
            "`/birthday upcoming` Browse upcoming visible birthdays.\n"
            "`/birthday month` Browse birthdays for a month.\n"
            "`/birthday twins` Find members who share your month and day.\n"
            "`/birthday timeline` View your birthday profile and celebration timeline.\n"
            "`/birthday wish add|list|remove` manage Birthday Capsule wishes.\n"
            "`/birthday capsule preview` previews your capsule privately.\n"
            "`/birthday quest status|check-in` tracks Birthday Quest progress.\n"
            "`/birthday list` remains available for private browsing."
        ),
        inline=False,
    )
    budget.add_field(
        name="Admin commands",
        value=(
            "`/birthday studio` is the main admin control surface.\n"
            "`/birthday setup` handles channel, timezone, and role safety.\n"
            "`/birthday test-message` Send a private operator dry run.\n"
            "`/birthday analytics` shows compact server analytics.\n"
            "`/birthday surprise queue|fulfill` manages manual Nitro concierge records.\n"
            "`/birthday member ...` View, set, or remove another member's record.\n"
            "`/birthday export` and `/birthday import` manage CSV backup and restore.\n"
            "`/birthday anniversary ...` manages tracked join anniversaries.\n"
            "`/birthday event ...` manages recurring annual celebrations.\n"
            "`/birthday health` Check permissions, config, and scheduler health."
        ),
        inline=False,
    )
    budget.add_field(
        name="Studio media and safety",
        value=(
            "Use `/birthday studio` -> Media Tools for shared image and thumbnail URLs.\n"
            "Direct media URLs can preview as embeds. Regular webpages are shown as webpage URLs "
            "and unsupported files are called out separately instead of being silently dropped.\n"
            "Birthday Quests can optionally count reactions on the shared birthday announcement "
            "post without using Message Content.\n"
            "Studio also blocks obvious profanity, NSFW wording, slurs, harassment-style text, "
            "and unsafe URL patterns."
        ),
        inline=False,
    )
    budget.add_field(
        name="Privacy",
        value=(
            "Birthdays stay scoped to the current server. Month/day is required, year is optional, "
            "and deletion is always available with `/birthday remove`. Birthday wishes stay "
            "private until reveal, and Nitro concierge is manual admin fulfillment only."
        ),
        inline=False,
    )
    budget.set_footer("Use top-level /help and /about for bot-wide info.")
    return budget.build()


def build_about_embed() -> discord.Embed:
    repo_url = _repository_url()
    package_version = _package_version()

    budget = BudgetedEmbed.create(
        title="\u2139\ufe0f About Bdayblaze",
        description=(
            "Bdayblaze helps Discord servers celebrate birthdays with server-scoped storage, "
            "private setup tools, and restart-safe scheduling."
        ),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="Privacy summary",
        value=(
            "Birthday data is stored per server membership in the current version. "
            "The bot stores month/day, an optional year, an optional timezone override, and a "
            "server-scoped visibility setting."
        ),
        inline=False,
    )
    budget.add_field(
        name="Deletion path",
        value=(
            "Members can delete their own stored birthday with `/birthday remove`. "
            "Admins can also remove records privately with `/birthday member remove`. "
            "CSV import/export is admin-only and delivered privately."
        ),
        inline=False,
    )
    budget.add_field(
        name="Permissions and config",
        value=(
            "Announcements and birthday roles only work when this server's settings, "
            "channel access, and role permissions allow them. Birthday Quest reactions use only "
            "the non-privileged guild reaction intent."
        ),
        inline=False,
    )
    budget.add_field(
        name="Health and uptime",
        value=(
            "Use `/birthday health` for server-specific issues. On hosted deployments, monitor "
            "`/readyz` for readiness and `/healthz` for detailed runtime state."
        ),
        inline=False,
    )
    version_line = f"Version: `{package_version}`"
    if repo_url is not None:
        version_line = f"{version_line}\nRepository: {repo_url}"
    budget.add_field(name="Version", value=version_line, inline=False)
    return budget.build()


def _package_version() -> str:
    try:
        return version("bdayblaze")
    except PackageNotFoundError:
        return "unavailable"


def _repository_url() -> str | None:
    try:
        package_metadata = metadata("bdayblaze")
    except PackageNotFoundError:
        return None
    project_urls = cast(list[str], package_metadata.get_all("Project-URL") or [])
    for value in project_urls:
        label, _, url = value.partition(",")
        if label.strip().lower() == "repository" and url.strip():
            return url.strip()
    return None

