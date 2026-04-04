from __future__ import annotations

from importlib.metadata import PackageNotFoundError

from bdayblaze.discord.ui import info


def test_build_help_embed_mentions_operator_flows() -> None:
    embed = info.build_help_embed()

    assert "Bdayblaze Help" in (embed.title or "")
    assert any(field.name == "Getting started" for field in embed.fields)
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
    assert "https://arno-create.github.io/Bdayblaze/help/" in (embed.footer.text or "")


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
    assert "1.2.3" in version_field.value
    assert "https://example.com/repo" in version_field.value
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
    assert "unavailable" in version_field.value
