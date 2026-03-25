from __future__ import annotations

import aiohttp
import pytest

from bdayblaze.domain.media_validation import (
    assess_media_url,
    mark_validated_direct_media_url,
    strip_validated_direct_media_marker,
)
from bdayblaze.services.media_validation_service import _probe_once, probe_media_url


def test_assess_media_url_accepts_realistic_signed_cdn_asset() -> None:
    assessment = assess_media_url(
        "https://cdn.example.com/assets/banner.gif?sig=abc123&expires=999",
        label="Announcement image",
    )

    assert assessment is not None
    assert assessment.classification == "direct_media"


def test_assess_media_url_marks_extensionless_object_url_for_validation() -> None:
    assessment = assess_media_url(
        "https://storage.example.com/object/banner?id=42&signature=abc",
        label="Announcement image",
    )

    assert assessment is not None
    assert assessment.classification == "needs_validation"


def test_assess_media_url_accepts_validated_extensionless_object_url() -> None:
    validated_url = mark_validated_direct_media_url(
        "https://storage.example.com/object/banner?id=42&signature=abc"
    )

    assessment = assess_media_url(
        validated_url,
        label="Announcement image",
    )

    assert assessment is not None
    assert assessment.classification == "direct_media"


def test_strip_validated_media_marker_restores_raw_url() -> None:
    raw_url = "https://storage.example.com/object/banner?id=42&signature=abc"

    assert strip_validated_direct_media_marker(
        mark_validated_direct_media_url(raw_url)
    ) == raw_url


def test_assess_media_url_rejects_webpage_suffix() -> None:
    assessment = assess_media_url(
        "https://www.example.com/gallery/photo-42.html",
        label="Announcement image",
    )

    assert assessment is not None
    assert assessment.classification == "webpage"


def test_assess_media_url_flags_unsupported_media_suffix() -> None:
    assessment = assess_media_url(
        "https://cdn.example.com/archive/video.mp4",
        label="Announcement image",
    )

    assert assessment is not None
    assert assessment.classification == "unsupported_media"


def test_assess_media_url_rejects_private_host_and_unsafe_tokens() -> None:
    private_host = assess_media_url(
        "https://127.0.0.1/banner.png",
        label="Announcement image",
    )
    unsafe_keyword = assess_media_url(
        "https://cdn.example.com/nsfw/banner.png",
        label="Announcement image",
    )

    assert private_host is not None
    assert private_host.classification == "invalid_or_unsafe"
    assert unsafe_keyword is not None
    assert unsafe_keyword.classification == "invalid_or_unsafe"


class _FakeContent:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def read(self, size: int) -> bytes:
        return self._payload[:size]


class _FakeResponse:
    def __init__(
        self,
        *,
        headers: dict[str, str],
        payload: bytes = b"",
        status: int = 200,
    ) -> None:
        self.headers = headers
        self.content = _FakeContent(payload)
        self.status = status


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def request(self, *args, **kwargs) -> _FakeRequestContext:
        return _FakeRequestContext(self._response)


class _SequenceSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses

    async def __aenter__(self) -> _SequenceSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def request(self, *args, **kwargs) -> _FakeRequestContext:
        return _FakeRequestContext(self._responses.pop(0))


@pytest.mark.asyncio
async def test_probe_once_accepts_image_content_type() -> None:
    assessment = assess_media_url(
        "https://cdn.example.com/banner",
        label="Announcement image",
    )
    assert assessment is not None

    result = await _probe_once(
        _FakeSession(
            _FakeResponse(headers={"Content-Type": "image/webp"})
        ),  # type: ignore[arg-type]
        assessment,
        method="HEAD",
        headers={},
    )

    assert result is not None
    assert result.classification == "direct_media"
    assert result.direct_render_expected is True


@pytest.mark.asyncio
async def test_probe_once_rejects_html_webpage() -> None:
    assessment = assess_media_url(
        "https://cdn.example.com/banner",
        label="Announcement image",
    )
    assert assessment is not None

    result = await _probe_once(
        _FakeSession(
            _FakeResponse(headers={"Content-Type": "text/html"})
        ),  # type: ignore[arg-type]
        assessment,
        method="HEAD",
        headers={},
    )

    assert result is not None
    assert result.classification == "webpage"


@pytest.mark.asyncio
async def test_probe_once_marks_404_as_invalid_media() -> None:
    assessment = assess_media_url(
        "https://cdn.example.com/banner",
        label="Announcement image",
    )
    assert assessment is not None

    result = await _probe_once(
        _FakeSession(
            _FakeResponse(headers={"Content-Type": "text/html"}, status=404)
        ),  # type: ignore[arg-type]
        assessment,
        method="GET",
        headers={},
        read_body=True,
    )

    assert result is not None
    assert result.classification == "invalid_or_unsafe"


@pytest.mark.asyncio
async def test_probe_once_uses_signature_fallback_for_octet_stream() -> None:
    assessment = assess_media_url(
        "https://cdn.example.com/banner",
        label="Announcement image",
    )
    assert assessment is not None

    result = await _probe_once(
        _FakeSession(
            _FakeResponse(
                headers={"Content-Type": "application/octet-stream"},
                payload=b"\x89PNG\r\n\x1a\nrest-of-file",
            )
        ),  # type: ignore[arg-type]
        assessment,
        method="GET",
        headers={},
        read_body=True,
    )

    assert result is not None
    assert result.classification == "direct_media"
    assert result.detected_kind == "png"


@pytest.mark.asyncio
async def test_probe_media_url_returns_validation_unavailable_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TimeoutSession:
        async def __aenter__(self) -> _TimeoutSession:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, *args, **kwargs) -> object:
            raise TimeoutError

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *args, **kwargs: _TimeoutSession())

    result = await probe_media_url(
        "https://storage.example.com/object/banner",
        label="Announcement image",
    )

    assert result is not None
    assert result.classification == "validation_unavailable"


@pytest.mark.asyncio
async def test_probe_media_url_falls_back_to_get_when_head_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _SequenceSession(
            [
                _FakeResponse(headers={"Content-Type": "text/html"}, status=403),
                _FakeResponse(headers={"Content-Type": "image/png"}, status=200),
            ]
        ),
    )

    result = await probe_media_url(
        "https://storage.example.com/object/banner",
        label="Announcement image",
    )

    assert result is not None
    assert result.classification == "direct_media"


@pytest.mark.asyncio
async def test_probe_media_url_rejects_internal_validation_marker_in_raw_input() -> None:
    result = await probe_media_url(
        mark_validated_direct_media_url("https://storage.example.com/object/banner?id=42"),
        label="Announcement image",
    )

    assert result is not None
    assert result.classification == "invalid_or_unsafe"
