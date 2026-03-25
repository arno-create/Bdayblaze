from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from bdayblaze.discord import studio_audit
from bdayblaze.discord.studio_audit import StudioAuditLogger
from bdayblaze.domain.models import GuildSettings
from bdayblaze.services.content_policy import ContentPolicyError, PolicyViolation


class FakeSettingsService:
    def __init__(self, settings: GuildSettings) -> None:
        self._settings = settings

    async def get_settings(self, guild_id: int) -> GuildSettings:
        assert guild_id == self._settings.guild_id
        return self._settings


class FakeTextChannel:
    def __init__(self) -> None:
        self.sent_embeds: list[object] = []

    def permissions_for(self, member: object) -> object:
        del member
        return SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)

    async def send(self, *, embed: object, allowed_mentions: object) -> None:
        del allowed_mentions
        self.sent_embeds.append(embed)


class FakeGuild:
    def __init__(self, channel: FakeTextChannel | None) -> None:
        self.id = 1
        self.me = SimpleNamespace()
        self._channel = channel

    def get_channel(self, channel_id: int) -> FakeTextChannel | None:
        if channel_id == 99:
            return self._channel
        return None


def _interaction(channel: FakeTextChannel | None) -> SimpleNamespace:
    return SimpleNamespace(
        guild=FakeGuild(channel),
        guild_id=1,
        user=SimpleNamespace(id=42, mention="@Operator"),
    )


def _error() -> ContentPolicyError:
    return ContentPolicyError(
        "Birthday announcement template contains blocked profane or vulgar language.",
        violations=(
            PolicyViolation(
                field_label="Birthday announcement template",
                rule_code="profanity",
                category_label="profane or vulgar language",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_studio_audit_logger_is_disabled_by_default() -> None:
    logger = StudioAuditLogger(
        FakeSettingsService(GuildSettings.default(1))
    )  # type: ignore[arg-type]

    await logger.log_blocked_attempt(
        _interaction(FakeTextChannel()),  # type: ignore[arg-type]
        surface="studio_template",
        error=_error(),
    )

    assert logger._seen_attempts == {}


@pytest.mark.asyncio
async def test_studio_audit_logger_dedupes_and_avoids_raw_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeTextChannel()
    settings = replace(GuildSettings.default(1), studio_audit_channel_id=99)
    logger = StudioAuditLogger(FakeSettingsService(settings))  # type: ignore[arg-type]
    monkeypatch.setattr(studio_audit.discord, "TextChannel", FakeTextChannel)
    interaction = _interaction(channel)

    await logger.log_blocked_attempt(
        interaction,  # type: ignore[arg-type]
        surface="studio_template",
        error=_error(),
    )
    await logger.log_blocked_attempt(
        interaction,  # type: ignore[arg-type]
        surface="studio_template",
        error=_error(),
    )

    assert len(channel.sent_embeds) == 1
    embed = channel.sent_embeds[0]
    assert embed.description == "A blocked Studio/admin content change was rejected by policy."
    values = "\n".join(field.value for field in embed.fields)
    assert "Birthday announcement template" in values
    assert "profane or vulgar language" in values
    assert "bitch" not in values


@pytest.mark.asyncio
async def test_studio_audit_logger_supports_manual_media_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeTextChannel()
    settings = replace(GuildSettings.default(1), studio_audit_channel_id=99)
    logger = StudioAuditLogger(FakeSettingsService(settings))  # type: ignore[arg-type]
    monkeypatch.setattr(studio_audit.discord, "TextChannel", FakeTextChannel)

    await logger.log_blocked_fields(
        _interaction(channel),  # type: ignore[arg-type]
        surface="studio_media",
        field_labels=("Announcement image",),
        category_labels=("unsafe media URL",),
        rule_codes=("unsafe_media_url",),
    )

    assert len(channel.sent_embeds) == 1
    values = "\n".join(field.value for field in channel.sent_embeds[0].fields)
    assert "unsafe media URL" in values
    assert "Announcement image" in values
