"""Pure, non-network URI validation and canonicalization for V3 sources."""

from __future__ import annotations

from collections.abc import Iterable
from posixpath import normpath
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.research import ResearchSourceType

_TRACKING_PARAMETERS = frozenset({"gclid", "fbclid"})
_WEB_TYPES = frozenset(
    {
        ResearchSourceType.URL,
        ResearchSourceType.DOCUMENTATION,
        ResearchSourceType.RSS,
    }
)
_FILE_TYPES = frozenset(
    {ResearchSourceType.FILE, ResearchSourceType.MARKDOWN, ResearchSourceType.PDF}
)


class InvalidResearchSourceUriError(ValueError):
    """The inert source registry cannot safely describe the supplied URI."""


def canonicalize_source_uri(source_type: ResearchSourceType, uri: str) -> str:
    """Return one stable identifier for duplicate detection without dereferencing it.

    This function parses strings only.  It does not resolve DNS, read a local
    path, spawn Git, or make any request, so accepting a URI here cannot turn
    source registration into ingestion.
    """
    if uri != uri.strip():
        raise InvalidResearchSourceUriError("uri must not begin or end with whitespace")
    try:
        parsed = urlsplit(uri)
    except ValueError as error:
        raise InvalidResearchSourceUriError("uri is malformed") from error
    source_scheme = parsed.scheme.lower()

    if source_type is ResearchSourceType.MANUAL:
        return _manual_uri(parsed, source_scheme)
    if source_type in _FILE_TYPES:
        return _file_uri(parsed, source_scheme)
    if source_type is ResearchSourceType.GIT:
        if source_scheme not in {"git", "https"}:
            raise InvalidResearchSourceUriError("git sources require a git or https URI")
        return _network_uri(parsed, source_scheme)
    if source_type is ResearchSourceType.GITHUB:
        if source_scheme != "https":
            raise InvalidResearchSourceUriError("github sources require an https URI")
        canonical = _network_uri(parsed, source_scheme)
        host = urlsplit(canonical).hostname
        if host not in {"github.com", "www.github.com"}:
            raise InvalidResearchSourceUriError("github sources must use github.com")
        return canonical
    if source_type in _WEB_TYPES:
        if source_scheme not in {"http", "https"}:
            raise InvalidResearchSourceUriError("web sources require an http or https URI")
        return _network_uri(parsed, source_scheme)
    raise InvalidResearchSourceUriError("unsupported source type")


def _manual_uri(parsed: SplitResult, scheme: str) -> str:
    value = parsed
    if scheme != "manual" or value.netloc or not value.path or value.query or value.fragment:
        raise InvalidResearchSourceUriError("manual sources require a manual: URI")
    return urlunsplit(("manual", "", value.path, "", ""))


def _file_uri(parsed: SplitResult, scheme: str) -> str:
    value = parsed
    if scheme != "file" or value.netloc or not value.path.startswith("/"):
        raise InvalidResearchSourceUriError("file-backed sources require an absolute file: URI")
    if value.query or value.fragment:
        raise InvalidResearchSourceUriError(
            "file-backed source URIs cannot contain query or fragment data"
        )
    return urlunsplit(("file", "", _normalized_path(value.path), "", ""))


def _network_uri(parsed: SplitResult, scheme: str) -> str:
    value = parsed
    if value.username is not None or value.password is not None:
        raise InvalidResearchSourceUriError("source URIs cannot contain credentials")
    try:
        host = value.hostname
        port = value.port
    except ValueError as error:
        raise InvalidResearchSourceUriError("uri has an invalid port") from error
    if host is None:
        raise InvalidResearchSourceUriError("network source URIs require a host")
    try:
        normalized_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise InvalidResearchSourceUriError("uri host is invalid") from error
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = normalized_host if port is None or default_port else f"{normalized_host}:{port}"
    query = _normalized_query(parse_qsl(value.query, keep_blank_values=True))
    return urlunsplit((scheme, netloc, _normalized_path(value.path), query, ""))


def _normalized_path(path: str) -> str:
    if not path:
        return "/"
    normalized = normpath(path)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if path.endswith("/") and normalized != "/":
        normalized += "/"
    return normalized


def _normalized_query(values: Iterable[tuple[str, str]]) -> str:
    retained = [
        (key, value)
        for key, value in values
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
    ]
    return urlencode(sorted(retained), doseq=True)
