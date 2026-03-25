from __future__ import annotations

from importlib.metadata import PackageNotFoundError

from bdayblaze.discord.ui import info


def test_build_help_embed_mentions_operator_flows() -> None:
    embed = info.build_help_embed()

    assert "Bdayblaze Help" in (embed.title or "")
    assert any(field.name == "Getting started" for field in embed.fields)
    assert "/birthday test-message" in embed.fields[0].value
    assert "/birthday studio" in embed.fields[0].value
    admin_field = next(field for field in embed.fields if field.name == "Admin commands")
    assert "/birthday import" in admin_field.value
    assert "/birthday anniversary ..." in admin_field.value
    safety_field = next(
        field for field in embed.fields if field.name == "Studio media and safety"
    )
    assert "Media Tools" in safety_field.value
    assert "Regular webpages" in safety_field.value


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
    assert "1.2.3" in version_field.value
    assert "https://example.com/repo" in version_field.value
    assert "/readyz" in health_field.value


def test_build_about_embed_handles_missing_package_metadata(monkeypatch) -> None:
    def raise_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(info, "version", raise_not_found)
    monkeypatch.setattr(info, "metadata", raise_not_found)

    embed = info.build_about_embed()

    version_field = next(field for field in embed.fields if field.name == "Version")
    assert "unavailable" in version_field.value
