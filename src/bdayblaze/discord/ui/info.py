from __future__ import annotations

from importlib.metadata import PackageNotFoundError, metadata, version
from typing import cast

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed

SUPPORT_SERVER_URL = "https://discord.com/servers/inevitable-friendship-1322933864360050688"
OFFICIAL_WEBSITE_URL = "https://arno-create.github.io/Bdayblaze/"
HELP_DOCS_URL = "https://arno-create.github.io/Bdayblaze/help/"
REPOSITORY_FALLBACK_URL = "https://github.com/arno-create/Bdayblaze"


def build_info_links_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Support Server",
            style=discord.ButtonStyle.link,
            url=SUPPORT_SERVER_URL,
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Website",
            style=discord.ButtonStyle.link,
            url=OFFICIAL_WEBSITE_URL,
        )
    )
    view.add_item(
        discord.ui.Button(
            label="GitHub",
            style=discord.ButtonStyle.link,
            url=_repository_url(),
        )
    )
    return view


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
            "2. Server admins should open `/birthdayadmin setup` for routing, timezone, "
            "and safety rules.\n"
            "3. Use `/birthdayadmin studio` for copy, style, previews, and celebration design.\n"
            "4. Use `/birthdayadmin test-message` before going live."
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
            "`/birthday month` Browse visible birthdays for a month.\n"
            "`/birthday twins` Find members who share your month and day.\n"
            "`/birthday timeline` View your timeline or a visible member's profile.\n"
            "`/birthday wish add|list|remove` manage your Birthday Capsule wishes.\n"
            "`/birthday capsule preview` previews your own capsule privately.\n"
            "`/birthday quest status|check-in` tracks Birthday Quest progress.\n"
            "`/birthday list` privately browses visible birthdays."
        ),
        inline=False,
    )
    budget.add_field(
        name="Admin commands",
        value=(
            "Admin tools moved to `/birthdayadmin ...` so regular members only see the public "
            "`/birthday` surface.\n"
            "`/birthdayadmin setup` handles delivery basics: routes, timezone, eligibility, "
            "and safety.\n"
            "`/birthdayadmin studio` handles celebration design: copy, Quiet vs Party style, "
            "previews, and annual-event polish.\n"
            "`/birthdayadmin test-message` Send a private operator dry run.\n"
            "`/birthdayadmin analytics` shows compact server analytics.\n"
            "`/birthdayadmin surprise queue|fulfill` manages manual Nitro concierge records.\n"
            "`/birthdayadmin member ...` View, set, or remove another member's record.\n"
            "`/birthdayadmin export` and `/birthdayadmin import` manage CSV backup and restore.\n"
            "`/birthdayadmin anniversary ...` manages tracked join anniversaries.\n"
            "`/birthdayadmin event ...` manages recurring annual celebrations.\n"
            "`/birthdayadmin month|list|timeline|wish remove|capsule preview` cover private "
            "admin browsing and moderation.\n"
            "`/birthdayadmin health` Check permissions, config, and scheduler health."
        ),
        inline=False,
    )
    budget.add_field(
        name="Studio media and safety",
        value=(
            "Use `/birthdayadmin studio` -> Media Tools for shared image and thumbnail URLs.\n"
            "Main Studio surfaces lead with live route, media source, and health instead of raw "
            "resolver traces.\n"
            "Direct media can preview as embeds. Regular webpages and unsupported files are "
            "called out clearly instead of being silently dropped.\n"
            "Birthday Quests can optionally count reactions on the shared birthday announcement "
            "post without using Message Content.\n"
            "Studio also blocks obvious profanity, NSFW wording, slurs, harassment-style text, "
            "and unsafe URL patterns."
        ),
        inline=False,
    )
    budget.add_field(
        name="Anniversary placeholders",
        value=(
            "`{anniversary.years}` -> Valid on member anniversary only.\n"
            "`{server_anniversary.years_since_creation}` -> Valid on server anniversary only.\n"
            "`{event.name}` / `{event.date}` / `{event.kind}` -> Valid on member anniversary, "
            "server anniversary, and recurring annual events."
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
    _add_links_field(budget)
    budget.set_footer(
        f"Public docs: {HELP_DOCS_URL} | Use top-level /about for bot-wide info."
    )
    return budget.build()


def build_support_embed() -> discord.Embed:
    budget = BudgetedEmbed.create(
        title="\U0001F6DF Bdayblaze Support",
        description=(
            "If you find a bug, hit a confusing edge case, or notice something that feels off, "
            "please report it. Those reports directly improve Bdayblaze and are genuinely "
            "appreciated."
        ),
        color=discord.Color.blurple(),
    )
    budget.add_field(
        name="How to get help",
        value=(
            "Use the support server for bug reports, setup help, and product questions. Sharing "
            "what happened, where it happened, and what you expected makes fixes much faster."
        ),
        inline=False,
    )
    _add_links_field(budget)
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
            "Admins can also remove records privately with `/birthdayadmin member remove`. "
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
            "Use `/birthdayadmin health` for server-specific issues. On hosted deployments, "
            "monitor `/readyz` for readiness and `/healthz` for detailed runtime state."
        ),
        inline=False,
    )
    budget.add_field(name="Version", value=f"Version: `{package_version}`", inline=False)
    _add_links_field(budget, repository_url=repo_url)
    return budget.build()


def _package_version() -> str:
    try:
        return version("bdayblaze")
    except PackageNotFoundError:
        return "unavailable"


def _add_links_field(
    budget: BudgetedEmbed,
    *,
    repository_url: str | None = None,
) -> None:
    budget.add_field(
        name="Links",
        value=_links_field_value(repository_url or _repository_url()),
        inline=False,
    )


def _links_field_value(repository_url: str) -> str:
    return (
        f"Support server: {SUPPORT_SERVER_URL}\n"
        f"Official website: {OFFICIAL_WEBSITE_URL}\n"
        f"GitHub: {repository_url}"
    )


def _repository_url() -> str:
    try:
        package_metadata = metadata("bdayblaze")
    except PackageNotFoundError:
        return REPOSITORY_FALLBACK_URL
    project_urls = cast(list[str], package_metadata.get_all("Project-URL") or [])
    for value in project_urls:
        label, _, url = value.partition(",")
        if label.strip().lower() == "repository" and url.strip():
            return url.strip()
    return REPOSITORY_FALLBACK_URL
