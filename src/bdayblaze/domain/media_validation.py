from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, urlparse

MAX_MEDIA_URL_LENGTH = 500
VALIDATED_DIRECT_MEDIA_FRAGMENT = "bdayblaze-media-ok"

ALLOWED_MEDIA_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
WEBPAGE_EXTENSIONS = frozenset({".htm", ".html"})
BLOCKED_MEDIA_EXTENSIONS = frozenset(
    {
        ".avi",
        ".bmp",
        ".css",
        ".csv",
        ".exe",
        ".ico",
        ".js",
        ".json",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".rar",
        ".svg",
        ".tar",
        ".txt",
        ".wav",
        ".webm",
        ".xml",
        ".zip",
    }
)
AMBIGUOUS_PAGE_EXTENSIONS = frozenset({".asp", ".aspx", ".cfm", ".cgi", ".jsp", ".jspx", ".php"})
TENOR_PAGE_HOSTS = frozenset({"tenor.com", "www.tenor.com"})
GIPHY_PAGE_HOSTS = frozenset({"giphy.com", "www.giphy.com"})
GOOGLE_HOSTS = frozenset({"google.com", "www.google.com", "images.google.com"})
GOOGLE_IMAGE_RESULT_QUERY_KEYS = frozenset({"imgrefurl", "imgurl"})
GENERIC_WRAPPER_QUERY_KEYS = frozenset({"imgrefurl", "imgurl", "mediaurl"})
UNSAFE_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)
UNSAFE_HOST_SUBSTRINGS = ("metadata",)
UNSAFE_HOST_SUFFIXES = (".internal", ".localhost", ".local")
UNSAFE_URL_TOKENS = frozenset(
    {
        "hentai",
        "nsfw",
        "onlyfans",
        "porn",
        "pornhub",
        "redtube",
        "rule34",
        "sex",
        "xhamster",
        "xvideos",
        "xxx",
    }
)

MediaClassification = str


@dataclass(slots=True, frozen=True)
class MediaUrlAssessment:
    label: str
    normalized_url: str
    classification: MediaClassification
    summary: str
    direct_render_expected: bool

    def status_label(self) -> str:
        return {
            "direct_media": "Direct media accepted",
            "webpage": "Webpage link rejected",
            "invalid_or_unsafe": "Invalid or unsafe URL rejected",
            "unsupported_media": "Unsupported media rejected",
            "needs_validation": "Needs validation",
        }[self.classification]


def assess_media_url(
    value: str | None,
    *,
    label: str,
    allow_validated_marker: bool = True,
) -> MediaUrlAssessment | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_MEDIA_URL_LENGTH:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL must be {MAX_MEDIA_URL_LENGTH} characters or fewer.",
            direct_render_expected=False,
        )
    if any(character.isspace() for character in normalized):
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL cannot contain spaces.",
            direct_render_expected=False,
        )
    parsed = urlparse(normalized)
    validated_marker = False
    if parsed.fragment:
        if parsed.fragment != VALIDATED_DIRECT_MEDIA_FRAGMENT or not allow_validated_marker:
            return MediaUrlAssessment(
                label=label,
                normalized_url=normalized,
                classification="invalid_or_unsafe",
                summary=f"{label} URL cannot include fragments.",
                direct_render_expected=False,
            )
        validated_marker = True
        parsed = parsed._replace(fragment="")
        normalized = parsed.geturl()
    if parsed.scheme != "https":
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL must use HTTPS.",
            direct_render_expected=False,
        )
    if not parsed.netloc or parsed.hostname is None:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL must point to a valid HTTPS host.",
            direct_render_expected=False,
        )
    if parsed.username or parsed.password:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL cannot include embedded credentials.",
            direct_render_expected=False,
        )
    host_issue = _host_issue(parsed.hostname)
    if host_issue is not None:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL {host_issue}.",
            direct_render_expected=False,
        )
    if _url_contains_unsafe_tokens(parsed):
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL contains blocked unsafe keywords.",
            direct_render_expected=False,
        )

    path = parsed.path or ""
    if not path or path == "/" or path.endswith("/"):
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL must include a media path.",
            direct_render_expected=False,
        )
    path_segment = path.rsplit("/", 1)[-1]
    if not path_segment or path_segment in {".", ".."}:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="invalid_or_unsafe",
            summary=f"{label} URL must include a valid media path.",
            direct_render_expected=False,
        )

    extension = path_extension(path_segment)
    webpage_summary = describe_webpage_media_issue(normalized, label=label)
    if webpage_summary is not None:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="webpage",
            summary=webpage_summary,
            direct_render_expected=False,
        )
    if extension in WEBPAGE_EXTENSIONS:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="webpage",
            summary=default_webpage_media_guidance(label),
            direct_render_expected=False,
        )
    if extension in BLOCKED_MEDIA_EXTENSIONS:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="unsupported_media",
            summary=(
                f"{label} URL points to unsupported {extension} content. "
                "Use a direct HTTPS image, GIF, or WebP file URL instead."
            ),
            direct_render_expected=False,
        )
    if extension in ALLOWED_MEDIA_EXTENSIONS:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="direct_media",
            summary=(
                f"{label} URL looks like a direct media file Discord can usually embed."
            ),
            direct_render_expected=True,
        )
    if validated_marker:
        return MediaUrlAssessment(
            label=label,
            normalized_url=value.strip(),
            classification="direct_media",
            summary=f"{label} URL was previously validated as direct media.",
            direct_render_expected=True,
        )
    if extension in AMBIGUOUS_PAGE_EXTENSIONS:
        return MediaUrlAssessment(
            label=label,
            normalized_url=normalized,
            classification="needs_validation",
            summary=(
                f"{label} URL uses a dynamic endpoint. Validate it before trusting live preview."
            ),
            direct_render_expected=False,
        )
    return MediaUrlAssessment(
        label=label,
        normalized_url=normalized,
        classification="needs_validation",
        summary=(
            f"{label} URL may still be direct media, but it needs validation first."
        ),
        direct_render_expected=False,
    )


def validate_media_url_candidate(
    value: str | None,
    *,
    label: str,
    allow_validated_marker: bool = True,
) -> str | None:
    assessment = assess_media_url(
        value,
        label=label,
        allow_validated_marker=allow_validated_marker,
    )
    if assessment is None:
        return None
    if assessment.classification == "invalid_or_unsafe":
        raise ValueError(assessment.summary)
    return assessment.normalized_url


def validate_direct_media_url(
    value: str | None,
    *,
    label: str,
    allow_validated_marker: bool = True,
) -> str | None:
    assessment = assess_media_url(
        value,
        label=label,
        allow_validated_marker=allow_validated_marker,
    )
    if assessment is None:
        return None
    if assessment.classification == "direct_media":
        return assessment.normalized_url
    if assessment.classification == "needs_validation":
        raise ValueError(
            f"{label} URL must be a direct media URL or be validated through Media Tools first."
        )
    raise ValueError(assessment.summary)


def mark_validated_direct_media_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    return parsed._replace(fragment=VALIDATED_DIRECT_MEDIA_FRAGMENT).geturl()


def strip_validated_direct_media_marker(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.fragment != VALIDATED_DIRECT_MEDIA_FRAGMENT:
        return normalized
    return parsed._replace(fragment="").geturl()


def sniff_media_signature(payload: bytes) -> str | None:
    if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
        return "gif"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    return None


def content_type_kind(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if not normalized:
        return None
    if normalized in {"image/gif", "image/jpeg", "image/jpg", "image/png", "image/webp"}:
        return normalized.rsplit("/", 1)[-1]
    if normalized.startswith("image/"):
        return "image"
    if normalized == "text/html":
        return "html"
    if normalized.startswith("text/"):
        return "text"
    return normalized


def describe_webpage_media_issue(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "").lower()
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = {name.lower() for name, _ in query_pairs}

    if host in TENOR_PAGE_HOSTS:
        return (
            f"{label} URL looks like a webpage, not a direct GIF/image file. "
            "For Tenor, use the direct media file URL, not the page link."
        )
    if host in GIPHY_PAGE_HOSTS:
        return (
            f"{label} URL looks like a webpage, not a direct GIF/image file. "
            "For Giphy, use the direct media file URL, not the page link."
        )
    if _is_google_image_result_wrapper(host, path, query_keys):
        return (
            f"{label} URL looks like a webpage, not a direct GIF/image file. "
            "Google image-result links are wrappers, not direct media files. "
            "Try copying the image/GIF address itself, not the browser page URL."
        )
    if query_keys.intersection(GENERIC_WRAPPER_QUERY_KEYS):
        return default_webpage_media_guidance(label)
    return None


def default_webpage_media_guidance(label: str) -> str:
    return (
        f"{label} URL looks like a webpage, not a direct GIF/image file. "
        "Try copying the image/GIF address itself, not the browser page URL."
    )


def path_extension(path_segment: str) -> str | None:
    stem, separator, suffix = path_segment.rpartition(".")
    if not separator or not stem or not suffix:
        return None
    return f".{suffix.lower()}"


def _is_google_image_result_wrapper(
    host: str,
    path: str,
    query_keys: set[str],
) -> bool:
    if host not in GOOGLE_HOSTS and not host.endswith(".google.com"):
        return False
    if path == "/imgres":
        return True
    if path == "/search" and "tbm" in query_keys:
        return True
    return bool(query_keys.intersection(GOOGLE_IMAGE_RESULT_QUERY_KEYS))


def _host_issue(hostname: str) -> str | None:
    normalized = hostname.strip().lower().rstrip(".")
    if not normalized:
        return "must include a public host"
    if normalized in UNSAFE_HOSTS:
        return "cannot use a local or metadata host"
    if any(normalized.endswith(suffix) for suffix in UNSAFE_HOST_SUFFIXES):
        return "cannot use a local or internal host"
    if any(token in normalized for token in UNSAFE_HOST_SUBSTRINGS):
        return "cannot use a metadata-style host"
    try:
        candidate = ip_address(normalized)
    except ValueError:
        return None
    if (
        candidate.is_loopback
        or candidate.is_link_local
        or candidate.is_private
        or candidate.is_reserved
        or candidate.is_multicast
        or candidate.is_unspecified
    ):
        return "cannot use a local or private IP address"
    return "must use a hostname instead of a raw IP address"


def _url_contains_unsafe_tokens(parsed: Any) -> bool:
    host = parsed.hostname or ""
    path = parsed.path or ""
    query = parsed.query or ""
    values = [host]
    values.extend(segment for segment in path.replace("/", " ").replace("-", " ").split())
    values.extend(name for name, _ in parse_qsl(query, keep_blank_values=True))
    values.extend(value for _, value in parse_qsl(query, keep_blank_values=True))
    tokens = {
        token
        for value in values
        for token in _tokenize(value)
    }
    return any(token in UNSAFE_URL_TOKENS for token in tokens)


def _tokenize(value: str) -> tuple[str, ...]:
    lowered = value.lower()
    token = []
    tokens: list[str] = []
    for character in lowered:
        if character.isalnum():
            token.append(character)
            continue
        if token:
            tokens.append("".join(token))
            token.clear()
    if token:
        tokens.append("".join(token))
    return tuple(tokens)
