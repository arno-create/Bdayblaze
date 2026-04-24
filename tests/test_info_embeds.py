from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import discord
import pytest

from bdayblaze.discord.ui import info


def _links_field(embed: discord.Embed) -> discord.EmbedField:
    return next(field for field in embed.fields if field.name == "Links")


def test_build_help_embed_mentions_operator_flows() -> None:
    embed = info.build_help_embed()

    assert "Bdayblaze Help" in (embed.title or "")
    assert any(field.name == "Getting started" for field in embed.fields)
    assert "/vote" in embed.fields[0].value
    assert "/birthdayadmin test-message" in embed.fields[0].value
    assert "/birthdayadmin studio" in embed.fields[0].value
    user_field = next(field for field in embed.fields if field.name == "User commands")
    assert "/birthday privacy" in user_field.value
    assert "/birthday capsule preview" in user_field.value
    admin_field = next(field for field in embed.fields if field.name == "Admin commands")
    assert "/birthdayadmin import" in admin_field.value
    assert "/birthdayadmin anniversary ..." in admin_field.value
    assert "moved to `/birthdayadmin ...`" in admin_field.value
    safety_field = next(
        field for field in embed.fields if field.name == "Studio media and safety"
    )
    anniversary_field = next(
        field for field in embed.fields if field.name == "Anniversary placeholders"
    )
    assert "Media Tools" in safety_field.value
    assert "live route, media source, and health" in safety_field.value
    assert "unsafe url patterns" in safety_field.value.lower()
    assert "shared birthday announcement post" in safety_field.value
    assert "{anniversary.years}" in anniversary_field.value
    assert "{server_anniversary.years_since_creation}" in anniversary_field.value
    links_field = _links_field(embed)
    assert info.SUPPORT_SERVER_URL in links_field.value
    assert info.OFFICIAL_WEBSITE_URL in links_field.value
    assert info.REPOSITORY_FALLBACK_URL in links_field.value
    assert info.HELP_DOCS_URL in (embed.footer.text or "")
    assert "premium" not in (embed.description or "").lower()


def test_build_support_embed_thanks_users_and_includes_links() -> None:
    embed = info.build_support_embed()

    assert "Bdayblaze Support" in (embed.title or "")
    assert "genuinely appreciated" in (embed.description or "").lower()
    help_field = next(field for field in embed.fields if field.name == "How to get help")
    links_field = _links_field(embed)

    assert "bug reports" in help_field.value.lower()
    assert "support server" in help_field.value.lower()
    assert info.SUPPORT_SERVER_URL in links_field.value
    assert info.OFFICIAL_WEBSITE_URL in links_field.value
    assert info.REPOSITORY_FALLBACK_URL in links_field.value


def test_build_about_embed_uses_package_metadata(monkeypatch) -> None:
    monkeypatch.setattr(info, "version", lambda _: "1.2.3")

    class FakeMetadata:
        @staticmethod
        def get_all(name: str) -> list[str]:
            assert name == "Project-URL"
            return ["Repository, https://example.com/repo"]

    monkeypatch.setattr(info, "metadata", lambda _: FakeMetadata())
    embed = info.build_about_embed()

    version_field = next(field for field in embed.fields if field.name == "Version")
    health_field = next(
        field for field in embed.fields if field.name == "Health and uptime"
    )
    deletion_field = next(field for field in embed.fields if field.name == "Deletion path")
    links_field = _links_field(embed)
    assert "1.2.3" in version_field.value
    assert "https://example.com/repo" not in version_field.value
    assert "https://example.com/repo" in links_field.value
    assert info.SUPPORT_SERVER_URL in links_field.value
    assert info.OFFICIAL_WEBSITE_URL in links_field.value
    assert "/readyz" in health_field.value
    assert "/birthdayadmin health" in health_field.value
    assert "/birthdayadmin member remove" in deletion_field.value


def test_build_about_embed_handles_missing_package_metadata(monkeypatch) -> None:
    def raise_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(info, "version", raise_not_found)
    monkeypatch.setattr(info, "metadata", raise_not_found)

    embed = info.build_about_embed()

    version_field = next(field for field in embed.fields if field.name == "Version")
    links_field = _links_field(embed)
    assert "unavailable" in version_field.value
    assert info.REPOSITORY_FALLBACK_URL in links_field.value


@pytest.mark.asyncio
async def test_build_info_links_view_uses_canonical_urls(monkeypatch) -> None:
    monkeypatch.setattr(info, "_repository_url", lambda: "https://example.com/repo")

    view = info.build_info_links_view()
    buttons = [
        (child.label, child.url, child.style)
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]

    assert buttons == [
        ("Support Server", info.SUPPORT_SERVER_URL, discord.ButtonStyle.link),
        ("Website", info.OFFICIAL_WEBSITE_URL, discord.ButtonStyle.link),
        ("GitHub", "https://example.com/repo", discord.ButtonStyle.link),
    ]
