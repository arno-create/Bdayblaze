from __future__ import annotations

from functools import lru_cache
from zoneinfo import available_timezones

COMMON_TIMEZONE_EXAMPLES: tuple[str, ...] = (
    "Asia/Yerevan",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
)


@lru_cache(maxsize=1)
def _all_timezones() -> tuple[str, ...]:
    return tuple(sorted(available_timezones()))


def timezone_examples_text() -> str:
    return ", ".join(COMMON_TIMEZONE_EXAMPLES)


def timezone_guidance(*, allow_server_default: bool) -> str:
    suffix = " Leave it blank to use the server default timezone." if allow_server_default else ""
    return f"Use an IANA timezone such as {timezone_examples_text()}.{suffix}"


def autocomplete_timezones(query: str, *, limit: int = 25) -> list[str]:
    normalized = query.strip().lower()
    if not normalized:
        return list(COMMON_TIMEZONE_EXAMPLES[:limit])

    ranked: list[tuple[int, int, int, str]] = []
    common_lookup = {value.lower(): index for index, value in enumerate(COMMON_TIMEZONE_EXAMPLES)}
    for timezone_name in _all_timezones():
        lowered = timezone_name.lower()
        if normalized not in lowered:
            continue
        common_rank = common_lookup.get(lowered, len(COMMON_TIMEZONE_EXAMPLES))
        prefix_rank = 0 if lowered.startswith(normalized) else 1
        path_rank = 0 if any(part.startswith(normalized) for part in lowered.split("/")) else 1
        ranked.append((common_rank, prefix_rank, path_rank, timezone_name))
    ranked.sort(key=lambda item: (*item[:3], item[3]))
    return [timezone_name for _, _, _, timezone_name in ranked[:limit]]
