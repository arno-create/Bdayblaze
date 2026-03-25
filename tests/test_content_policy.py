from __future__ import annotations

import pytest

from bdayblaze.services.content_policy import (
    ContentPolicyError,
    ensure_safe_announcement_inputs,
    ensure_safe_event_name,
    ensure_safe_template,
    ensure_safe_text,
)


@pytest.mark.parametrize(
    ("value", "label", "expected_category"),
    (
        (
            "Happy birthday you bitch",
            "Birthday announcement template",
            "profane or vulgar language",
        ),
        ("This is NSFW", "Announcement footer text", "sexual or NSFW language"),
        ("Go die already", "Announcement title override", "harassment or threat language"),
        ("You fag", "Recurring event name", "hateful or abusive slurs"),
    ),
)
def test_content_policy_blocks_unsafe_text(
    value: str,
    label: str,
    expected_category: str,
) -> None:
    with pytest.raises(ContentPolicyError) as exc_info:
        if label == "Birthday announcement template":
            ensure_safe_template(value, label=label)
        elif label == "Recurring event name":
            ensure_safe_event_name(value)
        else:
            ensure_safe_text(value, label=label)

    assert expected_category in str(exc_info.value)


def test_content_policy_allows_safe_text() -> None:
    ensure_safe_announcement_inputs(
        template="Happy birthday {birthday.names}",
        template_label="Birthday announcement template",
        title_override="Birthday Bulletin",
        footer_text="Powered by Bdayblaze",
        event_name="Server anniversary",
    )


def test_content_policy_normalizes_leetspeak() -> None:
    with pytest.raises(ContentPolicyError, match="sexual or NSFW language"):
        ensure_safe_text("n$fw celebration", label="Announcement title override")
