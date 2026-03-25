from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final

import aiohttp

from bdayblaze.domain.media_validation import (
    MediaUrlAssessment,
    assess_media_url,
    content_type_kind,
    default_webpage_media_guidance,
    describe_webpage_media_issue,
    sniff_media_signature,
)

_DEFAULT_TIMEOUT_SECONDS: Final = 4
_MAX_REDIRECTS: Final = 2
_SIGNATURE_BYTES: Final = 64
_PROBE_HEADERS: Final[dict[str, str]] = {
    "Accept": "image/*, */*;q=0.1",
    "User-Agent": "BdayblazeMediaProbe/1.0",
}


@dataclass(slots=True, frozen=True)
class MediaProbeResult:
    label: str
    url: str
    classification: str
    summary: str
    direct_render_expected: bool
    content_type: str | None = None
    detected_kind: str | None = None

    def status_label(self) -> str:
        return {
            "direct_media": "Direct media accepted",
            "webpage": "Webpage link rejected",
            "invalid_or_unsafe": "Invalid or unsafe URL rejected",
            "unsupported_media": "Unsupported media rejected",
            "validation_unavailable": "Validation unavailable",
        }[self.classification]


async def probe_media_url(value: str | None, *, label: str) -> MediaProbeResult | None:
    assessment = assess_media_url(
        value,
        label=label,
        allow_validated_marker=False,
    )
    if assessment is None:
        return None
    if assessment.classification == "invalid_or_unsafe":
        return MediaProbeResult(
            label=label,
            url=assessment.normalized_url,
            classification=assessment.classification,
            summary=assessment.summary,
            direct_render_expected=False,
        )
    if assessment.classification == "webpage":
        return MediaProbeResult(
            label=label,
            url=assessment.normalized_url,
            classification="webpage",
            summary=assessment.summary,
            direct_render_expected=False,
        )
    if assessment.classification == "unsupported_media":
        return MediaProbeResult(
            label=label,
            url=assessment.normalized_url,
            classification="unsupported_media",
            summary=assessment.summary,
            direct_render_expected=False,
        )

    timeout = aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            head_result = await _probe_once(
                session,
                assessment,
                method="HEAD",
                headers=_PROBE_HEADERS,
            )
            if head_result is not None:
                return head_result
            return await _probe_once(
                session,
                assessment,
                method="GET",
                headers={**_PROBE_HEADERS, "Range": f"bytes=0-{_SIGNATURE_BYTES - 1}"},
                read_body=True,
            )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return MediaProbeResult(
            label=label,
            url=assessment.normalized_url,
            classification="validation_unavailable",
            summary=(
                f"{label} URL could not be verified right now. "
                "Try again later or use a stable direct media URL."
            ),
            direct_render_expected=False,
        )


async def _probe_once(
    session: aiohttp.ClientSession,
    assessment: MediaUrlAssessment,
    *,
    method: str,
    headers: dict[str, str],
    read_body: bool = False,
) -> MediaProbeResult | None:
    async with session.request(
        method,
        assessment.normalized_url,
        allow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        headers=headers,
    ) as response:
        status = getattr(response, "status", 200)
        if status >= 400:
            if method == "HEAD":
                return None
            if status in {408, 425, 429} or status >= 500:
                return MediaProbeResult(
                    label=assessment.label,
                    url=assessment.normalized_url,
                    classification="validation_unavailable",
                    summary=(
                        f"{assessment.label} URL could not be verified right now "
                        f"(HTTP {status}). Try again later."
                    ),
                    direct_render_expected=False,
                )
            return MediaProbeResult(
                label=assessment.label,
                url=assessment.normalized_url,
                classification="invalid_or_unsafe",
                summary=(
                    f"{assessment.label} URL could not be fetched as direct media "
                    f"(HTTP {status})."
                ),
                direct_render_expected=False,
            )
        content_type = response.headers.get("Content-Type")
        kind = content_type_kind(content_type)
        if kind in {"gif", "image", "jpeg", "png", "webp"}:
            return MediaProbeResult(
                label=assessment.label,
                url=assessment.normalized_url,
                classification="direct_media",
                summary=f"{assessment.label} URL responded as direct media.",
                direct_render_expected=True,
                content_type=content_type,
                detected_kind=kind,
            )
        if kind == "html":
            return MediaProbeResult(
                label=assessment.label,
                url=assessment.normalized_url,
                classification="webpage",
                summary=(
                    describe_webpage_media_issue(
                        assessment.normalized_url,
                        label=assessment.label,
                    )
                    or default_webpage_media_guidance(assessment.label)
                ),
                direct_render_expected=False,
                content_type=content_type,
                detected_kind=kind,
            )
        if not read_body:
            return None
        payload = await response.content.read(_SIGNATURE_BYTES)
        detected_kind = sniff_media_signature(payload)
        if detected_kind is not None:
            return MediaProbeResult(
                label=assessment.label,
                url=assessment.normalized_url,
                classification="direct_media",
                summary=f"{assessment.label} URL matched a direct media file signature.",
                direct_render_expected=True,
                content_type=content_type,
                detected_kind=detected_kind,
            )
        classification = "webpage" if kind == "text" else "unsupported_media"
        summary = (
            (
                describe_webpage_media_issue(
                    assessment.normalized_url,
                    label=assessment.label,
                )
                or default_webpage_media_guidance(assessment.label)
            )
            if classification == "webpage"
            else (
                f"{assessment.label} URL did not verify as a supported image, GIF, or WebP asset."
            )
        )
        return MediaProbeResult(
            label=assessment.label,
            url=assessment.normalized_url,
            classification=classification,
            summary=summary,
            direct_render_expected=False,
            content_type=content_type,
            detected_kind=kind,
        )
