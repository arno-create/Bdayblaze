from __future__ import annotations

from importlib.metadata import PackageNotFoundError, metadata, version

import discord


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Bdayblaze help",
        description=(
            "Birthday tracking for Discord servers with privacy-first defaults and "
            "lightweight admin controls."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Getting started",
        value=(
            "1. Save your date with `/birthday set`.\n"
            "2. Server admins can open `/birthday setup`.\n"
            "3. Customize the message in `/birthday message`.\n"
            "4. Use `/birthday test-message` before going live."
        ),
        inline=False,
    )
    embed.add_field(
        name="User commands",
        value=(
            "`/birthday set` Save your birthday for this server.\n"
            "`/birthday view` See your saved record.\n"
            "`/birthday remove` Delete your server-scoped data.\n"
            "`/birthday today` Show birthdays currently active under bot celebration logic.\n"
            "`/birthday next` See the nearest upcoming birthday.\n"
            "`/birthday month` Browse birthdays for a month.\n"
            "`/birthday twins` Find members who share your month and day."
        ),
        inline=False,
    )
    embed.add_field(
        name="Admin commands",
        value=(
            "`/birthday setup` Configure channel, timezone, and role behavior.\n"
            "`/birthday message` Edit the announcement template and theme.\n"
            "`/birthday test-message` Send a private operator preview.\n"
            "`/birthday member ...` View, set, or remove another member's record.\n"
            "`/birthday list` Privately browse stored birthdays.\n"
            "`/birthday health` Check permissions, config, and scheduler health."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value=(
            "Birthdays stay scoped to the current server. Month/day is required, year is optional, "
            "and deletion is always available with `/birthday remove`."
        ),
        inline=False,
    )
    embed.set_footer(text="Top-level /help and /about aliases are available too.")
    return embed


def build_about_embed() -> discord.Embed:
    repo_url = _repository_url()
    package_version = _package_version()

    embed = discord.Embed(
        title="About Bdayblaze",
        description=(
            "Bdayblaze helps Discord servers celebrate birthdays with server-scoped storage, "
            "private setup tools, and restart-safe scheduling."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Privacy summary",
        value=(
            "Birthday data is stored per server membership in the current version. "
            "The bot stores month/day, an optional year, and an optional timezone override."
        ),
        inline=False,
    )
    embed.add_field(
        name="Deletion path",
        value=(
            "Members can delete their own stored birthday with `/birthday remove`. "
            "Admins can also remove records privately with `/birthday member remove`."
        ),
        inline=False,
    )
    embed.add_field(
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
    embed.add_field(name="Version", value=version_line, inline=False)
    return embed


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
    for value in package_metadata.get_all("Project-URL", []):
        label, _, url = value.partition(",")
        if label.strip().lower() == "repository" and url.strip():
            return url.strip()
    return None
