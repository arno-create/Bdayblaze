from __future__ import annotations

import pytest

from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    MULTIPLE_TIMEZONES_LABEL,
    AnnouncementRenderRecipient,
    normalize_announcement_template,
    render_announcement_template,
    validate_announcement_template,
)


def _recipient(
    *,
    mention: str,
    display_name: str,
    username: str,
    month: int,
    day: int,
    timezone: str,
) -> AnnouncementRenderRecipient:
    return AnnouncementRenderRecipient(
        mention=mention,
        display_name=display_name,
        username=username,
        birth_month=month,
        birth_day=day,
        timezone=timezone,
    )


def test_validate_template_rejects_unknown_placeholders() -> None:
    with pytest.raises(ValueError, match=r"\{user.secret\}"):
        validate_announcement_template("Hello {user.secret}")


def test_validate_template_rejects_unmatched_braces() -> None:
    with pytest.raises(ValueError, match="unmatched"):
        validate_announcement_template("Hello {birthday.names")


def test_validate_template_treats_blank_as_default_reset() -> None:
    assert validate_announcement_template("   ") is None
    assert normalize_announcement_template(None) == DEFAULT_ANNOUNCEMENT_TEMPLATE


def test_render_template_supports_escaped_literal_braces() -> None:
    rendered = render_announcement_template(
        "Use {{braces}} for fun, {user.display_name}.",
        server_name="Birthday Club",
        celebration_mode="quiet",
        recipients=[
            _recipient(
                mention="@Arman",
                display_name="Arman",
                username="arman",
                month=3,
                day=24,
                timezone="Asia/Yerevan",
            )
        ],
    )

    assert rendered == "Use {braces} for fun, Arman."


def test_render_template_handles_single_user_placeholders() -> None:
    rendered = render_announcement_template(
        "Happy birthday {user.display_name} in {server.name} on {birthday.date} ({timezone})!",
        server_name="Birthday Club",
        celebration_mode="quiet",
        recipients=[
            _recipient(
                mention="@Arman",
                display_name="Arman",
                username="arman",
                month=3,
                day=24,
                timezone="Asia/Yerevan",
            )
        ],
    )

    assert rendered == "Happy birthday Arman in Birthday Club on March 24 (Asia/Yerevan)!"


def test_render_template_handles_batched_aliases_safely() -> None:
    rendered = render_announcement_template(
        (
            "{user.mention} are up today in {server.name}. "
            "{birthday.count} birthdays, {birthday.names}, {timezone}."
        ),
        server_name="Birthday Club",
        celebration_mode="party",
        recipients=[
            _recipient(
                mention="@Arman",
                display_name="Arman",
                username="arman",
                month=3,
                day=24,
                timezone="Asia/Yerevan",
            ),
            _recipient(
                mention="@Lia",
                display_name="Lia",
                username="lia",
                month=3,
                day=24,
                timezone="Asia/Tokyo",
            ),
        ],
    )

    assert "@Arman @Lia" in rendered
    assert "2 birthdays" in rendered
    assert "Arman, Lia" in rendered
    assert MULTIPLE_TIMEZONES_LABEL in rendered
