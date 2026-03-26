from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from bdayblaze.domain.models import GuildSettings
from bdayblaze.services import diagnostics


class FakeTextChannel:
    def __init__(self, name: str, permissions: object) -> None:
        self.name = name
        self._permissions = permissions

    def permissions_for(self, member: object) -> object:
        return self._permissions


class FakeRole:
    def __init__(self, *, position: int, managed: bool = False, default: bool = False) -> None:
        self.position = position
        self.managed = managed
        self._default = default

    def __le__(self, other: object) -> bool:
        if not isinstance(other, FakeRole):
            return NotImplemented
        return self.position <= other.position

    def is_default(self) -> bool:
        return self._default


class FakeGuild:
    def __init__(
        self,
        *,
        me: object,
        channel: object | None = None,
        role: object | None = None,
    ) -> None:
        self.me = me
        self._channel = channel
        self._role = role

    def get_channel(self, channel_id: int) -> object | None:
        return self._channel

    def get_role(self, role_id: int) -> object | None:
        return self._role


class FakeMember:
    def __init__(
        self,
        *,
        bot: bool = False,
        joined_at: datetime | None = None,
        roles: list[object] | None = None,
    ) -> None:
        self.bot = bot
        self.joined_at = joined_at
        self.roles = roles or []


def test_build_channel_diagnostics_reports_specific_missing_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnostics.discord, "TextChannel", FakeTextChannel)
    channel = FakeTextChannel(
        "birthdays",
        SimpleNamespace(view_channel=False, send_messages=False, embed_links=False),
    )
    guild = FakeGuild(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=FakeRole(position=10),
        ),
        channel=channel,
    )

    issues = diagnostics.build_channel_diagnostics(
        guild,  # type: ignore[arg-type]
        channel_id=123,
        label="announcement",
    )

    assert [issue.code for issue in issues] == [
        "missing_view_channel",
        "missing_send_messages",
        "missing_embed_links",
    ]


def test_build_role_diagnostics_reports_manage_roles_and_hierarchy() -> None:
    guild = FakeGuild(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=False),
            top_role=FakeRole(position=1),
        ),
        role=FakeRole(position=5),
    )

    issues = diagnostics.build_role_diagnostics(guild, role_id=55)  # type: ignore[arg-type]

    assert [issue.code for issue in issues] == ["manage_roles_missing"]


def test_build_role_diagnostics_reports_hierarchy_problem() -> None:
    guild = FakeGuild(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=FakeRole(position=1),
        ),
        role=FakeRole(position=5),
    )

    issues = diagnostics.build_role_diagnostics(guild, role_id=55)  # type: ignore[arg-type]

    assert [issue.code for issue in issues] == ["role_hierarchy_invalid"]


def test_evaluate_member_eligibility_reports_bot_ignore() -> None:
    decision = diagnostics.evaluate_member_eligibility(
        settings=GuildSettings.default(1),
        member=FakeMember(bot=True),  # type: ignore[arg-type]
        now_utc=datetime(2026, 3, 24, tzinfo=UTC),
    )

    assert decision.allowed is False
    assert decision.code == "bot_ignored"


def test_evaluate_member_eligibility_reports_missing_role() -> None:
    settings = replace(GuildSettings.default(1), eligibility_role_id=99)
    member = FakeMember(
        joined_at=datetime.now(UTC) - timedelta(days=365),
        roles=[],
    )

    decision = diagnostics.evaluate_member_eligibility(
        settings=settings,
        member=member,  # type: ignore[arg-type]
        now_utc=datetime(2026, 3, 24, tzinfo=UTC),
    )

    assert decision.allowed is False
    assert decision.code == "eligibility_role_missing"


def test_evaluate_member_eligibility_reports_minimum_membership_age() -> None:
    settings = replace(GuildSettings.default(1), minimum_membership_days=30)
    member = FakeMember(joined_at=datetime.now(UTC) - timedelta(days=5))

    decision = diagnostics.evaluate_member_eligibility(
        settings=settings,
        member=member,  # type: ignore[arg-type]
        now_utc=datetime(2026, 3, 24, tzinfo=UTC),
    )

    assert decision.allowed is False
    assert decision.code == "membership_age_unmet"


def test_build_presentation_diagnostics_reports_invalid_media() -> None:
    settings = GuildSettings.default(1)

    diagnostics_result = diagnostics.build_presentation_diagnostics(
        settings.presentation(image_url="https://example.com/manual.pdf")
    )

    assert [item.code for item in diagnostics_result] == ["announcement_image_invalid"]


def test_build_presentation_diagnostics_reports_needs_validation_for_ambiguous_media() -> None:
    settings = GuildSettings.default(1)

    diagnostics_result = diagnostics.build_presentation_diagnostics(
        settings.presentation(image_url="https://cdn.example.com/assets/banner?sig=abc123")
    )

    assert [item.code for item in diagnostics_result] == [
        "announcement_image_invalid_needs_validation"
    ]


def test_classify_discord_http_failure_marks_invalid_payload_as_permanent() -> None:
    response = SimpleNamespace(status=400, reason="Bad Request")
    error = discord.HTTPException(
        response,  # type: ignore[arg-type]
        {"code": 50035, "message": "Invalid Form Body"},
    )

    failure = diagnostics.classify_discord_http_failure(error, surface="announcement")

    assert failure.permanent is True
    assert failure.code == "invalid_announcement_payload"
