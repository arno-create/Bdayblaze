from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Final
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

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
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
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
        connector = aiohttp.TCPConnector(resolver=_PublicResolver())
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
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
    except (aiohttp.ClientError, TimeoutError):
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
    current_assessment = assessment
    redirect_count = 0
    while True:
        try:
            resolution_issue = await _url_resolution_issue(current_assessment.normalized_url)
        except _ResolutionUnavailable:
            return _validation_unavailable(current_assessment)
        if resolution_issue is not None:
            return MediaProbeResult(
                label=current_assessment.label,
                url=current_assessment.normalized_url,
                classification="invalid_or_unsafe",
                summary=f"{current_assessment.label} URL {resolution_issue}.",
                direct_render_expected=False,
            )
        response_context = session.request(
            method,
            current_assessment.normalized_url,
            allow_redirects=False,
            headers=headers,
        )

        async with response_context as response:
            status = getattr(response, "status", 200)
            if status in _REDIRECT_STATUSES:
                redirect = _assess_redirect(
                    current_assessment,
                    response.headers.get("Location"),
                    redirect_count=redirect_count,
                )
                if isinstance(redirect, MediaProbeResult):
                    return redirect
                current_assessment = redirect
                redirect_count += 1
                continue
            return await _classify_probe_response(
                response,
                current_assessment,
                method=method,
                read_body=read_body,
            )


async def _classify_probe_response(
    response: aiohttp.ClientResponse,
    assessment: MediaUrlAssessment,
    *,
    method: str,
    read_body: bool,
) -> MediaProbeResult | None:
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
        else f"{assessment.label} URL did not verify as a supported image, GIF, or WebP asset."
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


def _assess_redirect(
    assessment: MediaUrlAssessment,
    location: str | None,
    *,
    redirect_count: int,
) -> MediaUrlAssessment | MediaProbeResult:
    if not location:
        return MediaProbeResult(
            label=assessment.label,
            url=assessment.normalized_url,
            classification="invalid_or_unsafe",
            summary=f"{assessment.label} URL redirected without a valid destination.",
            direct_render_expected=False,
        )
    if redirect_count >= _MAX_REDIRECTS:
        return MediaProbeResult(
            label=assessment.label,
            url=assessment.normalized_url,
            classification="invalid_or_unsafe",
            summary=f"{assessment.label} URL redirected too many times.",
            direct_render_expected=False,
        )
    redirected_url = urljoin(assessment.normalized_url, location)
    redirected = assess_media_url(
        redirected_url,
        label=assessment.label,
        allow_validated_marker=False,
    )
    if redirected is None or redirected.classification == "invalid_or_unsafe":
        summary = (
            redirected.summary
            if redirected is not None
            else f"{assessment.label} URL redirected to an empty destination."
        )
        return MediaProbeResult(
            label=assessment.label,
            url=redirected_url,
            classification="invalid_or_unsafe",
            summary=f"{assessment.label} URL redirected to an unsafe target: {summary}",
            direct_render_expected=False,
        )
    if redirected.classification in {"webpage", "unsupported_media"}:
        return MediaProbeResult(
            label=assessment.label,
            url=redirected.normalized_url,
            classification=redirected.classification,
            summary=(
                f"{assessment.label} URL redirected to an unsupported target: "
                f"{redirected.summary}"
            ),
            direct_render_expected=False,
        )
    return redirected


async def _url_resolution_issue(value: str) -> str | None:
    parsed = urlparse(value)
    hostname = parsed.hostname
    if hostname is None:
        return "must point to a valid HTTPS host"
    try:
        return await _public_resolution_issue(hostname)
    except _ResolutionUnavailable:
        raise


async def _public_resolution_issue(hostname: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise _ResolutionUnavailable from exc
    if not infos:
        raise _ResolutionUnavailable
    for info in infos:
        sockaddr = info[4]
        resolved = sockaddr[0]
        issue = _resolved_ip_issue(resolved)
        if issue is not None:
            return issue
    return None


def _resolved_ip_issue(value: str) -> str | None:
    try:
        candidate = ip_address(value)
    except ValueError:
        return "resolved to an invalid IP address"
    if (
        candidate.is_loopback
        or candidate.is_link_local
        or candidate.is_private
        or candidate.is_reserved
        or candidate.is_multicast
        or candidate.is_unspecified
    ):
        return "resolved to a local or private IP address"
    return None


def _validation_unavailable(assessment: MediaUrlAssessment) -> MediaProbeResult:
    return MediaProbeResult(
        label=assessment.label,
        url=assessment.normalized_url,
        classification="validation_unavailable",
        summary=(
            f"{assessment.label} URL could not be verified right now. "
            "Try again later or use a stable direct media URL."
        ),
        direct_render_expected=False,
    )


class _ResolutionUnavailable(Exception):
    pass


class _PublicResolver(AbstractResolver):
    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise _ResolutionUnavailable from exc

        results: list[ResolveResult] = []
        for family_value, _type, proto, _canonname, sockaddr in infos:
            resolved = sockaddr[0]
            issue = _resolved_ip_issue(resolved)
            if issue is not None:
                raise OSError(issue)
            results.append(
                {
                    "hostname": host,
                    "host": resolved,
                    "port": sockaddr[1],
                    "family": family_value,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not results:
            raise OSError("host did not resolve")
        return results

    async def close(self) -> None:
        return None
