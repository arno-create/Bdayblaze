from __future__ import annotations

from bdayblaze.domain.timezones import (
    COMMON_TIMEZONE_EXAMPLES,
    autocomplete_timezones,
    timezone_guidance,
)


def test_autocomplete_timezones_prefers_curated_examples_for_blank_query() -> None:
    assert autocomplete_timezones("")[: len(COMMON_TIMEZONE_EXAMPLES)] == list(
        COMMON_TIMEZONE_EXAMPLES
    )


def test_autocomplete_timezones_finds_common_timezone_by_partial_query() -> None:
    suggestions = autocomplete_timezones("los")

    assert "America/Los_Angeles" in suggestions


def test_timezone_guidance_mentions_server_default_when_allowed() -> None:
    guidance = timezone_guidance(allow_server_default=True)

    assert "Asia/Yerevan" in guidance
    assert "Leave it blank to use the server default" in guidance
