from __future__ import annotations

from importlib.metadata import PackageNotFoundError, metadata, version
from typing import cast

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed


def build_help_embed() -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="📘 Bdayblaze Help",
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
            "2. Server admins can open `/birthday setup`.\n"
            "3. Open Celebration Studio with `/birthday studio`.\n"
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
            "`/birthday today` Show birthdays currently active under bot celebration logic.\n"
            "`/birthday next` See the nearest upcoming birthday.\n"
            "`/birthday upcoming` Browse upcoming visible birthdays.\n"
            "`/birthday month` Browse birthdays for a month.\n"
            "`/birthday twins` Find members who share your month and day.\n"
            "`/birthday list` Browse visible birthdays privately."
        ),
        inline=False,
    )
    budget.add_field(
        name="Admin commands",
        value=(
            "`/birthday setup` Configure channel, timezone, and role behavior.\n"
            "`/birthday studio` Open Celebration Studio.\n"
            "`/birthday test-message` Send a private operator dry run.\n"
            "`/birthday member ...` View, set, or remove another member's record.\n"
            "`/birthday export` and `/birthday import` manage CSV backup and restore.\n"
            "`/birthday anniversary ...` manages tracked join anniversaries.\n"
            "`/birthday event ...` manages recurring annual celebrations.\n"
            "`/birthday health` Check permissions, config, and scheduler health."
        ),
        inline=False,
    )
    budget.add_field(
        name="Privacy",
        value=(
            "Birthdays stay scoped to the current server. Month/day is required, year is optional, "
            "and deletion is always available with `/birthday remove`."
        ),
        inline=False,
    )
    budget.set_footer("Use top-level /help and /about for bot-wide info.")
    return budget.build()


def build_about_embed() -> discord.Embed:
    repo_url = _repository_url()
    package_version = _package_version()

    budget = BudgetedEmbed.create(
        title="📌 About Bdayblaze",
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
            "channel access, "
            "and role permissions allow them."
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
    project_urls = cast(list[str], package_metadata.get_all("Project-URL", []))
    for value in project_urls:
        label, _, url = value.partition(",")
        if label.strip().lower() == "repository" and url.strip():
            return url.strip()
    return None
