from __future__ import annotations

import pytest

from bdayblaze.domain.announcement_template import (
    DEFAULT_ANNOUNCEMENT_TEMPLATE,
    MULTIPLE_TIMEZONES_LABEL,
    SERVER_ANNIVERSARY_YEARS_PLACEHOLDER,
    AnnouncementRenderContext,
    AnnouncementRenderRecipient,
    normalize_announcement_template,
    render_announcement_template,
    validate_accent_color,
    validate_announcement_template,
    validate_media_url,
)


def _recipient(
    *,
    mention: str,
    display_name: str,
    username: str,
    month: int | None = 3,
    day: int | None = 24,
    timezone: str | None = "Asia/Yerevan",
    anniversary_years: int | None = None,
) -> AnnouncementRenderRecipient:
    return AnnouncementRenderRecipient(
        mention=mention,
        display_name=display_name,
        username=username,
        birth_month=month,
        birth_day=day,
        timezone=timezone,
        anniversary_years=anniversary_years,
    )


def _context(
    *,
    kind: str = "birthday_announcement",
    recipients: list[AnnouncementRenderRecipient] | None = None,
    late_delivery: bool = False,
) -> AnnouncementRenderContext:
    return AnnouncementRenderContext(
        kind=kind,  # type: ignore[arg-type]
        server_name="Birthday Club",
        celebration_mode="quiet",
        recipients=recipients
        or [
            _recipient(
                mention="@Arman",
                display_name="Arman",
                username="arman",
            )
        ],
        event_name="Join anniversary" if kind == "anniversary" else None,
        event_month=3,
        event_day=24,
        late_delivery=late_delivery,
    )


def test_validate_template_rejects_unknown_placeholders() -> None:
    with pytest.raises(ValueError, match=r"\{user.secret\}"):
        validate_announcement_template("Hello {user.secret}", kind="birthday_announcement")


def test_validate_template_rejects_unmatched_braces() -> None:
    with pytest.raises(ValueError, match="unmatched"):
        validate_announcement_template("Hello {birthday.names", kind="birthday_announcement")


def test_validate_template_treats_blank_as_default_reset() -> None:
    assert validate_announcement_template("   ", kind="birthday_announcement") is None
    assert (
        normalize_announcement_template(None, kind="birthday_announcement")
        == DEFAULT_ANNOUNCEMENT_TEMPLATE
    )


def test_validate_template_rejects_member_anniversary_years_on_server_anniversary() -> None:
    with pytest.raises(
        ValueError,
        match=r"Use \{server_anniversary\.years_since_creation\} instead",
    ):
        validate_announcement_template(
            "Happy {anniversary.years}",
            kind="server_anniversary",
        )


def test_validate_template_rejects_server_anniversary_years_on_member_anniversary() -> None:
    with pytest.raises(
        ValueError,
        match=r"Use \{anniversary\.years\} instead",
    ):
        validate_announcement_template(
            f"Happy {{{SERVER_ANNIVERSARY_YEARS_PLACEHOLDER}}}",
            kind="anniversary",
        )


def test_validate_template_rejects_event_placeholders_on_birthday_surfaces() -> None:
    with pytest.raises(ValueError, match=r"not valid for Birthday announcement templates"):
        validate_announcement_template(
            "Today is {event.name}",
            kind="birthday_announcement",
        )


def test_render_template_supports_escaped_literal_braces() -> None:
    rendered = render_announcement_template(
        "Use {{braces}} for fun, {user.display_name}.",
        context=_context(),
    )

    assert rendered == "Use {braces} for fun, Arman."


def test_render_template_handles_batched_aliases_safely() -> None:
    rendered = render_announcement_template(
        (
            "{user.mention} are up today in {server.name}. "
            "{birthday.count} birthdays, {birthday.names}, {timezone}."
        ),
        context=_context(
            recipients=[
                _recipient(
                    mention="@Arman",
                    display_name="Arman",
                    username="arman",
                    timezone="Asia/Yerevan",
                ),
                _recipient(
                    mention="@Lia",
                    display_name="Lia",
                    username="lia",
                    timezone="Asia/Tokyo",
                ),
            ]
        ),
    )

    assert "@Arman @Lia" in rendered
    assert "2 birthdays" in rendered
    assert "Arman, Lia" in rendered
    assert MULTIPLE_TIMEZONES_LABEL in rendered


def test_render_template_adds_anniversary_specific_values() -> None:
    rendered = render_announcement_template(
        "Happy {event.kind} to {members.names} for {anniversary.years} years.",
        context=_context(
            kind="anniversary",
            recipients=[
                _recipient(
                    mention="@Jamie",
                    display_name="Jamie",
                    username="jamie",
                    month=None,
                    day=None,
                    timezone=None,
                    anniversary_years=4,
                )
            ],
        ),
    )

    assert rendered == "Happy anniversary to Jamie for 4 years."


def test_render_template_surfaces_recovery_note_placeholder() -> None:
    rendered = render_announcement_template(
        "{delivery.note} Happy birthday {birthday.names}!",
        context=_context(late_delivery=True),
    )

    assert "missed the exact moment" in rendered


def test_render_template_supports_server_anniversary_years_placeholder() -> None:
    rendered = render_announcement_template(
        f"Server age: {{{SERVER_ANNIVERSARY_YEARS_PLACEHOLDER}}}",
        context=AnnouncementRenderContext(
            kind="server_anniversary",
            server_name="Birthday Club",
            celebration_mode="party",
            recipients=[],
            event_name="Server anniversary",
            event_month=4,
            event_day=1,
            server_anniversary_years_since_creation=6,
        ),
    )

    assert rendered == "Server age: 6"


def test_render_template_requires_server_creation_years_for_server_anniversary() -> None:
    with pytest.raises(ValueError, match="needs the server creation date"):
        render_announcement_template(
            f"Server age: {{{SERVER_ANNIVERSARY_YEARS_PLACEHOLDER}}}",
            context=AnnouncementRenderContext(
                kind="server_anniversary",
                server_name="Birthday Club",
                celebration_mode="party",
                recipients=[],
                event_name="Server anniversary",
                event_month=4,
                event_day=1,
            ),
        )


def test_validate_media_url_accepts_https_image_with_query_string() -> None:
    assert (
        validate_media_url(
            "https://cdn.example.com/happy.gif?size=512",
            label="Announcement image",
        )
        == "https://cdn.example.com/happy.gif?size=512"
    )


def test_validate_media_url_rejects_unvalidated_signed_extensionless_media_path() -> None:
    with pytest.raises(ValueError, match="Media Tools first"):
        validate_media_url(
            "https://cdn.example.com/assets/birthday-banner?sig=abc123&expires=999",
            label="Announcement image",
        )


def test_validate_media_url_rejects_unvalidated_dynamic_media_endpoint() -> None:
    with pytest.raises(ValueError, match="Media Tools first"):
        validate_media_url(
            "https://images.example.com/render.php?id=42&format=gif",
            label="Announcement image",
        )


def test_validate_media_url_rejects_non_https_values() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_media_url("http://example.com/happy.png", label="Announcement image")


def test_validate_media_url_rejects_missing_file_path() -> None:
    with pytest.raises(ValueError, match="must include a media path"):
        validate_media_url("https://cdn.example.com/", label="Announcement image")


def test_validate_media_url_rejects_non_image_suffixes() -> None:
    with pytest.raises(ValueError, match=r"unsupported \.pdf content"):
        validate_media_url("https://cdn.example.com/invite.pdf", label="Announcement image")


def test_validate_accent_color_parses_hex_values() -> None:
    assert validate_accent_color("#FFB347") == 0xFFB347


def test_validate_accent_color_rejects_invalid_hex() -> None:
    with pytest.raises(ValueError, match="6-digit hex"):
        validate_accent_color("#GGGGGG")


def test_render_template_uses_context_dates() -> None:
    rendered = render_announcement_template(
        "{event.date}",
        context=AnnouncementRenderContext(
            kind="recurring_event",
            server_name="Birthday Club",
            celebration_mode="party",
            recipients=[],
            event_name="Server birthday",
            event_month=3,
            event_day=25,
            late_delivery=False,
        ),
    )

    assert rendered == "March 25"
