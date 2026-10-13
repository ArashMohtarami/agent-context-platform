"""Constrained, inert content providers for V3 research ingestion.

Manual and file providers only process bytes supplied to the API.  URL acquisition
is isolated in :class:`URLSourceProvider`: it permits exactly one registered
HTTP(S) resource, validates every redirect target, and turns HTML into inert,
bounded Markdown-like text.  No provider opens a server-side path, invokes a
shell, runs JavaScript, or executes source content.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from typing import Final, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from app.models.common import Metadata
from app.models.research import ResearchSourceDocument, ResearchSourceType
from app.research.uri import InvalidResearchSourceUriError, canonicalize_source_uri

MAX_INGESTION_BYTES: Final = 700_000
MAX_EXTRACTED_CHARACTERS: Final = 200_000
MAX_PDF_PAGES: Final = 100
MAX_PDF_STREAM_BYTES: Final = 500_000
TEXT_CONTENT_TYPES: Final[frozenset[str]] = frozenset({"text/plain", "text/markdown"})
MARKDOWN_CONTENT_TYPES: Final[frozenset[str]] = frozenset({"text/markdown", "text/x-markdown"})
PDF_CONTENT_TYPES: Final[frozenset[str]] = frozenset({"application/pdf", "application/x-pdf"})
_ARCHIVE_SIGNATURES: Final[tuple[bytes, ...]] = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf'\x1c",
    b"Rar!\x1a\x07",
)
_EXECUTABLE_SIGNATURES: Final[tuple[bytes, ...]] = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe")
_EXECUTABLE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".apk", ".com", ".dll", ".dmg", ".exe", ".msi", ".scr"}
)

# URL acquisition is deliberately more constrained than file ingestion.  The
# limits cap both memory use and the total time a request can occupy a worker.
MAX_URL_RESPONSE_BYTES: Final = MAX_INGESTION_BYTES
MAX_URL_REDIRECTS: Final = 3
URL_CONNECT_TIMEOUT_SECONDS: Final = 3.0
URL_READ_TIMEOUT_SECONDS: Final = 5.0
URL_TOTAL_TIMEOUT_SECONDS: Final = 10.0
APPROVED_URL_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"text/html", "application/xhtml+xml"}
)
_CLOUD_METADATA_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "metadata.google.internal",
        "metadata.google.com",
        "metadata.azure.com",
        "instance-data",
    }
)


class SourceProviderError(ValueError):
    """A caller-safe refusal while validating or parsing untrusted source data."""


@dataclass(frozen=True, slots=True)
class IngestionPayload:
    """A bounded submitted body; it never names a path the server should open."""

    data: bytes
    content_type: str
    title: str
    language: str
    filename: str | None = None
    acquire_url: bool = False
    acquire_repository: bool = False

    @property
    def source_hash(self) -> str:
        """Return a stable hash of the exact submitted bytes for the snapshot."""
        return sha256(self.data).hexdigest()


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One text page with optional source heading anchors keyed by source line."""

    text: str
    page: int | None
    heading_anchors: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedResearchDocument:
    """Normalized content and the locations that remain available for citations."""

    title: str
    content_type: str
    language: str
    content: str
    content_hash: str
    source_hash: str
    pages: tuple[ExtractedPage, ...]
    retrieved_at: datetime | None = None
    version_identifier: str | None = None
    snapshot_metadata: Metadata | None = None
    locator_url: str | None = None


class SourceProvider(Protocol):
    """A narrow async contract; acquisition authority stays provider-specific."""

    def supports(self, source_type: ResearchSourceType) -> bool: ...

    async def extract(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedResearchDocument: ...


def _normalized_text(data: bytes) -> str:
    try:
        decoded = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise SourceProviderError("text sources must be valid UTF-8") from error
    if "\x00" in decoded:
        raise SourceProviderError("text sources must not contain NUL bytes")
    text = decoded.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise SourceProviderError("source content must not be empty")
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise SourceProviderError("extracted text exceeds the ingestion limit")
    return text


def _document(
    payload: IngestionPayload,
    *,
    content_type: str,
    pages: tuple[ExtractedPage, ...],
) -> ExtractedResearchDocument:
    content = "\n\f\n".join(page.text for page in pages if page.text).strip()
    if not content:
        raise SourceProviderError("the document does not contain extractable text")
    if len(content) > MAX_EXTRACTED_CHARACTERS:
        raise SourceProviderError("extracted text exceeds the ingestion limit")
    return ExtractedResearchDocument(
        title=payload.title,
        content_type=content_type,
        language=payload.language,
        content=content,
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
        source_hash=payload.source_hash,
        pages=pages,
    )


def _validate_payload_size(payload: IngestionPayload) -> None:
    if not payload.data:
        raise SourceProviderError("source content must not be empty")
    if len(payload.data) > MAX_INGESTION_BYTES:
        raise SourceProviderError("source data exceeds the ingestion size limit")


def _validate_uploaded_file(payload: IngestionPayload) -> None:
    """Reject paths, archives, and executable bytes before parser dispatch."""
    if payload.filename is not None:
        filename = payload.filename.strip()
        if not filename or any(character in filename for character in ("/", "\\", "\x00")):
            raise SourceProviderError("uploaded filename is unsafe")
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if f".{suffix}" in _EXECUTABLE_SUFFIXES:
            raise SourceProviderError("executable uploads are not supported")
    if payload.data.startswith(_ARCHIVE_SIGNATURES) or (
        len(payload.data) >= 262 and payload.data[257:262] == b"ustar"
    ):
        raise SourceProviderError("archive uploads are not supported")
    if payload.data.startswith(_EXECUTABLE_SIGNATURES):
        raise SourceProviderError("executable uploads are not supported")


class ManualSourceProvider:
    """Normalize explicit UTF-8 text supplied in a JSON ingestion request."""

    _types: Final[frozenset[ResearchSourceType]] = frozenset(
        {ResearchSourceType.MANUAL, ResearchSourceType.MARKDOWN}
    )

    def supports(self, source_type: ResearchSourceType) -> bool:
        return source_type in self._types

    async def extract(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedResearchDocument:
        if not self.supports(source.source_type):
            raise SourceProviderError("this source type does not accept manual text")
        _validate_payload_size(payload)
        content_type = payload.content_type.lower().split(";", 1)[0].strip()
        if content_type not in TEXT_CONTENT_TYPES:
            raise SourceProviderError("manual content must be text/plain or text/markdown")
        if (
            source.source_type is ResearchSourceType.MARKDOWN
            and content_type not in MARKDOWN_CONTENT_TYPES
        ):
            raise SourceProviderError("a Markdown source requires a Markdown content type")
        text = _normalized_text(payload.data)
        return _document(
            payload,
            content_type="text/markdown"
            if content_type in MARKDOWN_CONTENT_TYPES
            else "text/plain",
            pages=(ExtractedPage(text=text, page=None),),
        )


class FileSourceProvider:
    """Extract allowed uploaded text or PDF bytes without filesystem access."""

    _types: Final[frozenset[ResearchSourceType]] = frozenset(
        {ResearchSourceType.FILE, ResearchSourceType.MARKDOWN, ResearchSourceType.PDF}
    )

    def supports(self, source_type: ResearchSourceType) -> bool:
        return source_type in self._types

    async def extract(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedResearchDocument:
        if not self.supports(source.source_type):
            raise SourceProviderError("this source type is not supported for file ingestion")
        _validate_payload_size(payload)
        _validate_uploaded_file(payload)
        content_type = payload.content_type.lower().split(";", 1)[0].strip()
        is_pdf = content_type in PDF_CONTENT_TYPES
        if source.source_type is ResearchSourceType.PDF and not is_pdf:
            raise SourceProviderError("a PDF source requires application/pdf")
        if is_pdf:
            if source.source_type not in {ResearchSourceType.FILE, ResearchSourceType.PDF}:
                raise SourceProviderError("a Markdown source cannot contain PDF data")
            if not payload.data.startswith(b"%PDF-"):
                raise SourceProviderError("uploaded data is not a valid PDF")
            return _document(
                payload, content_type="application/pdf", pages=_extract_pdf(payload.data)
            )
        if content_type not in TEXT_CONTENT_TYPES:
            raise SourceProviderError("file ingestion supports only plain text, Markdown, and PDF")
        if source.source_type is ResearchSourceType.PDF:
            raise SourceProviderError("a PDF source requires PDF data")
        if (
            source.source_type is ResearchSourceType.MARKDOWN
            and content_type not in MARKDOWN_CONTENT_TYPES
        ):
            raise SourceProviderError("a Markdown source requires a Markdown content type")
        text = _normalized_text(payload.data)
        normalized_type = (
            "text/markdown" if content_type in MARKDOWN_CONTENT_TYPES else "text/plain"
        )
        return _document(payload, content_type=normalized_type, pages=(ExtractedPage(text, None),))


type Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
type ClientFactory = Callable[[], httpx.AsyncClient]


class URLSourceProvider:
    """Fetch one registered public HTTP(S) URL with bounded address checks.

    This is not a crawler.  It follows at most ``MAX_URL_REDIRECTS`` redirects,
    performs DNS and connected-peer checks at every hop, requests identity
    encoding only, and accepts HTML/XHTML only.  The client opts out of ambient
    proxy configuration so an operator's proxy cannot silently change this
    isolation boundary.
    """

    _types: Final[frozenset[ResearchSourceType]] = frozenset({ResearchSourceType.URL})

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        client_factory: ClientFactory | None = None,
        total_timeout_seconds: float = URL_TOTAL_TIMEOUT_SECONDS,
    ) -> None:
        if total_timeout_seconds <= 0:
            raise ValueError("URL total timeout must be positive")
        self._resolver = resolver or _resolve_public_host
        self._client_factory = client_factory or _url_client
        self._total_timeout_seconds = total_timeout_seconds

    def supports(self, source_type: ResearchSourceType) -> bool:
        return source_type in self._types

    async def extract(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedResearchDocument:
        if not self.supports(source.source_type):
            raise SourceProviderError("this source type is not supported for URL ingestion")
        source_url = source.canonical_uri or source.uri
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                return await self._fetch_html(source_url, source)
        except TimeoutError as error:
            raise SourceProviderError("URL request exceeded the total time limit") from error
        except httpx.TimeoutException as error:
            raise SourceProviderError("URL request exceeded a network timeout") from error
        except httpx.HTTPError as error:
            raise SourceProviderError("URL request could not be completed") from error

    async def _fetch_html(
        self, initial_url: str, source: ResearchSourceDocument
    ) -> ExtractedResearchDocument:
        current_url = initial_url
        redirects = 0
        async with self._client_factory() as client:
            while True:
                allowed_ips = await self._safe_url(current_url)
                async with client.stream(
                    "GET",
                    current_url,
                    headers={
                        "Accept": "text/html, application/xhtml+xml;q=0.9",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    _validate_connected_peer(response, allowed_ips)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirects >= MAX_URL_REDIRECTS:
                            raise SourceProviderError("URL redirect limit exceeded")
                        location = response.headers.get("location")
                        if location is None or not location.strip():
                            raise SourceProviderError(
                                "URL redirect response did not include a location"
                            )
                        current_url = urljoin(current_url, location)
                        redirects += 1
                        continue
                    if not 200 <= response.status_code < 300:
                        raise SourceProviderError("URL returned a non-success response")
                    content_type = _response_content_type(response.headers.get("content-type"))
                    if content_type not in APPROVED_URL_CONTENT_TYPES:
                        raise SourceProviderError("URL response content type is not approved")
                    _validate_response_headers(response)
                    body = await _read_bounded_response(response)
                    return _normalize_html_response(
                        body,
                        source=source,
                        response=response,
                        retrieved_url=current_url,
                        redirect_count=redirects,
                    )

    async def _safe_url(
        self, value: str
    ) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise SourceProviderError("URL is malformed") from error
        if parsed.scheme.lower() not in {"http", "https"}:
            raise SourceProviderError("URL ingestion supports only http and https")
        if parsed.username is not None or parsed.password is not None:
            raise SourceProviderError("URL credentials are not permitted")
        try:
            host = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise SourceProviderError("URL port is invalid") from error
        if host is None:
            raise SourceProviderError("URL must include a host")
        normalized_host = host.rstrip(".").lower()
        if (
            normalized_host == "localhost"
            or normalized_host.endswith(".localhost")
            or normalized_host in _CLOUD_METADATA_HOSTS
        ):
            raise SourceProviderError("URL host is not permitted")
        effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            literal = ipaddress.ip_address(normalized_host)
        except ValueError:
            addresses = await self._resolver(normalized_host, effective_port)
        else:
            # A literal cannot be made safe by a resolver test double, a proxy,
            # or a later DNS answer. Validate it directly before connecting.
            addresses = (str(literal),)
        if not addresses:
            raise SourceProviderError("URL host did not resolve to an address")
        result: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as error:
                raise SourceProviderError("URL DNS response was invalid") from error
            if not address.is_global:
                raise SourceProviderError("URL host resolves to a forbidden address")
            result.add(address)
        return frozenset(result)


async def _resolve_public_host(host: str, port: int) -> Sequence[str]:
    """Resolve just before each request, so every redirect is independently checked."""
    try:
        records = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise SourceProviderError("URL host could not be resolved") from error
    return tuple(str(record[4][0]) for record in records)


def _url_client() -> httpx.AsyncClient:
    """Create the deliberately small direct client used by URL ingestion."""
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(
            URL_TOTAL_TIMEOUT_SECONDS,
            connect=URL_CONNECT_TIMEOUT_SECONDS,
            read=URL_READ_TIMEOUT_SECONDS,
            write=URL_CONNECT_TIMEOUT_SECONDS,
            pool=URL_CONNECT_TIMEOUT_SECONDS,
        ),
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
        headers={"User-Agent": "AgentContextPlatformResearch/3"},
    )


def _validate_connected_peer(
    response: httpx.Response,
    allowed_ips: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> None:
    """Reject a connection that did not reach the checked DNS result.

    HTTPX exposes the peer on its network stream for real direct connections.
    Mock transports do not have one, so this remains testable without relaxing
    production checks.  It narrows the DNS-rebinding window without introducing
    an unsafe custom TLS transport.
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    peer = stream.get_extra_info("server_addr")
    if not isinstance(peer, tuple) or not peer:
        return
    try:
        address = ipaddress.ip_address(str(peer[0]))
    except ValueError as error:
        raise SourceProviderError("URL connection peer was invalid") from error
    if not address.is_global or address not in allowed_ips:
        raise SourceProviderError("URL connection did not reach an approved address")


def _response_content_type(value: str | None) -> str:
    if value is None:
        return ""
    return value.split(";", 1)[0].strip().lower()


def _validate_response_headers(response: httpx.Response) -> None:
    content_encoding = response.headers.get("content-encoding", "").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise SourceProviderError("compressed URL responses are not supported")
    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    try:
        length = int(content_length)
    except ValueError as error:
        raise SourceProviderError("URL response content length was invalid") from error
    if length < 0 or length > MAX_URL_RESPONSE_BYTES:
        raise SourceProviderError("URL response exceeds the size limit")


async def _read_bounded_response(response: httpx.Response) -> bytes:
    data = bytearray()
    async for chunk in response.aiter_raw():
        data.extend(chunk)
        if len(data) > MAX_URL_RESPONSE_BYTES:
            raise SourceProviderError("URL response exceeds the size limit")
    if not data:
        raise SourceProviderError("URL response was empty")
    return bytes(data)


class _HtmlNormalizer(HTMLParser):
    """Extract visible HTML text and heading locations without evaluating HTML."""

    _ignored: Final[frozenset[str]] = frozenset({"script", "style", "template", "noscript"})
    _block: Final[frozenset[str]] = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "div",
            "dl",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "header",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "td",
            "th",
            "tr",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.heading_anchors: list[tuple[int, str]] = []
        self._current: list[str] = []
        self._ignored_depth = 0
        self._head_depth = 0
        self._body_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._heading: tuple[int, str | None] | None = None
        self.author: str | None = None
        self.publisher: str | None = None
        self.published_at: str | None = None
        self.declared_canonical: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        values = {key.lower(): value for key, value in attrs}
        if lowered in self._ignored:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if lowered == "head":
            self._head_depth += 1
            return
        if lowered == "body":
            self._body_depth += 1
            return
        if lowered == "title":
            self._in_title = True
            return
        if lowered == "meta":
            self._metadata(values)
            return
        if lowered == "link":
            rel = (values.get("rel") or "").lower().split()
            href = values.get("href")
            if "canonical" in rel and href:
                self.declared_canonical = href.strip()[:2_048]
            return
        if lowered in {"br", "hr"}:
            self._flush()
            return
        if lowered in self._block:
            self._flush()
            return
        if len(lowered) == 2 and lowered[0] == "h" and lowered[1] in "123456":
            self._flush()
            self._heading = (int(lowered[1]), values.get("id"))

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if lowered == "head" and self._head_depth:
            self._head_depth -= 1
            return
        if lowered == "body" and self._body_depth:
            self._body_depth -= 1
            return
        if lowered == "title":
            self._in_title = False
            return
        if self._heading is not None and lowered == f"h{self._heading[0]}":
            self._flush(heading=self._heading)
            self._heading = None
            return
        if lowered in self._block:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._head_depth and not self._body_depth:
            return
        self._current.append(data)

    @property
    def title(self) -> str | None:
        return _clean_html_text(" ".join(self._title_parts)) or None

    def _metadata(self, values: dict[str, str | None]) -> None:
        key = (values.get("name") or values.get("property") or values.get("itemprop") or "").lower()
        value = _clean_html_text(values.get("content") or "")
        if not value:
            return
        if key in {"author", "article:author"} and self.author is None:
            self.author = value[:500]
        elif key in {"og:site_name", "application-name", "publisher"} and self.publisher is None:
            self.publisher = value[:500]
        elif key in {"article:published_time", "date", "datepublished", "publishdate"}:
            parsed = _parse_publication_date(value)
            if parsed is not None:
                self.published_at = parsed

    def _flush(self, *, heading: tuple[int, str | None] | None = None) -> None:
        text = _clean_html_text(" ".join(self._current))
        self._current = []
        if not text:
            return
        if heading is None:
            self.lines.append(text)
            return
        level, supplied_anchor = heading
        anchor = _safe_anchor(supplied_anchor) or _slug_anchor(text)
        self.lines.append(f"{'#' * level} {text}")
        self.heading_anchors.append((len(self.lines), anchor))


def _clean_html_text(value: str) -> str:
    return " ".join(value.split())


def _safe_anchor(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 1_024:
        return None
    return cleaned


def _slug_anchor(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:1_024]
    return slug or "section"


def _parse_publication_date(value: str) -> str | None:
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _canonical_metadata_url(base: str, candidate: str | None) -> str | None:
    if not candidate:
        return None
    try:
        return canonicalize_source_uri(ResearchSourceType.URL, urljoin(base, candidate))
    except InvalidResearchSourceUriError:
        return None


def _normalize_html_response(
    body: bytes,
    *,
    source: ResearchSourceDocument,
    response: httpx.Response,
    retrieved_url: str,
    redirect_count: int,
) -> ExtractedResearchDocument:
    try:
        decoded = body.decode(response.encoding or "utf-8", errors="strict")
    except (LookupError, UnicodeDecodeError) as error:
        raise SourceProviderError("URL HTML must use a supported text encoding") from error
    parser = _HtmlNormalizer()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception as error:
        raise SourceProviderError("URL HTML could not be normalized") from error
    parser._flush()
    normalized = "\n\n".join(parser.lines).strip()
    if not normalized:
        raise SourceProviderError("URL HTML did not contain extractable text")
    if len(normalized) > MAX_EXTRACTED_CHARACTERS:
        raise SourceProviderError("extracted text exceeds the ingestion limit")
    canonical_url = _canonical_metadata_url(retrieved_url, retrieved_url) or retrieved_url
    declared_canonical = _canonical_metadata_url(retrieved_url, parser.declared_canonical)
    publisher = parser.publisher or (urlsplit(canonical_url).hostname or None)
    title = parser.title or source.title
    metadata: Metadata = {
        "source_content_type": _response_content_type(response.headers.get("content-type")),
        "canonical_url": canonical_url,
        "retrieved_url": retrieved_url,
        "title": title,
        "publisher": publisher,
        "redirect_count": redirect_count,
    }
    if declared_canonical is not None:
        metadata["declared_canonical_url"] = declared_canonical
    if parser.author is not None:
        metadata["author"] = parser.author
    if parser.published_at is not None:
        metadata["published_at"] = parser.published_at
    etag = response.headers.get("etag")
    last_modified = response.headers.get("last-modified")
    if etag:
        metadata["etag"] = etag[:2_048]
    if last_modified:
        metadata["last_modified"] = last_modified[:2_048]
    version_identifier = (
        f"etag:{etag[:2_000]}"
        if etag
        else f"last-modified:{last_modified[:2_000]}"
        if last_modified
        else None
    )
    return ExtractedResearchDocument(
        title=title,
        content_type="text/markdown",
        language="und",
        content=normalized,
        content_hash=sha256(normalized.encode("utf-8")).hexdigest(),
        source_hash=sha256(body).hexdigest(),
        pages=(
            ExtractedPage(
                text=normalized,
                page=None,
                heading_anchors=tuple(parser.heading_anchors),
            ),
        ),
        retrieved_at=datetime.now(UTC),
        version_identifier=version_identifier,
        snapshot_metadata=metadata,
        locator_url=canonical_url,
    )


def _extract_pdf(data: bytes) -> tuple[ExtractedPage, ...]:
    """Extract bounded text from simple PDF streams without a subprocess or file access."""
    return _extract_basic_pdf(data)


_PAGE_PATTERN: Final = re.compile(rb"/Type\s*/Page\b")
_STREAM_PATTERN: Final = re.compile(
    rb"(?P<prefix>.{0,512}?)stream\r?\n(?P<body>.*?)\r?\nendstream", re.DOTALL
)
_LITERAL_TJ_PATTERN: Final = re.compile(rb"\((?P<text>(?:\\.|[^\\)])*)\)\s*Tj")
_ARRAY_TJ_PATTERN: Final = re.compile(rb"\[(?P<items>.*?)\]\s*TJ", re.DOTALL)
_LITERAL_PATTERN: Final = re.compile(rb"\((?P<text>(?:\\.|[^\\)])*)\)")


def _bounded_flate(data: bytes) -> bytes:
    decoder = zlib.decompressobj()
    output = decoder.decompress(data, MAX_PDF_STREAM_BYTES + 1)
    if len(output) > MAX_PDF_STREAM_BYTES or decoder.unconsumed_tail:
        raise SourceProviderError("PDF content stream exceeds the parser limit")
    output += decoder.flush(MAX_PDF_STREAM_BYTES + 1 - len(output))
    if len(output) > MAX_PDF_STREAM_BYTES:
        raise SourceProviderError("PDF content stream exceeds the parser limit")
    return output


def _pdf_literal(value: bytes) -> str:
    out = bytearray()
    position = 0
    while position < len(value):
        current = value[position]
        if current != 92 or position + 1 >= len(value):  # backslash
            out.append(current)
            position += 1
            continue
        position += 1
        escaped = value[position]
        escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        out.append(escapes.get(escaped, escaped))
        position += 1
    return bytes(out).decode("latin-1", errors="replace")


def _stream_text(stream: bytes) -> str:
    values = [_pdf_literal(item.group("text")) for item in _LITERAL_TJ_PATTERN.finditer(stream)]
    for array in _ARRAY_TJ_PATTERN.finditer(stream):
        values.extend(
            _pdf_literal(item.group("text"))
            for item in _LITERAL_PATTERN.finditer(array.group("items"))
        )
    return " ".join(value for value in values if value).strip()


def _extract_basic_pdf(data: bytes) -> tuple[ExtractedPage, ...]:
    if b"/Encrypt" in data:
        raise SourceProviderError("encrypted PDFs are not supported")
    page_count = len(_PAGE_PATTERN.findall(data))
    if not 1 <= page_count <= MAX_PDF_PAGES:
        raise SourceProviderError("uploaded data is not a readable PDF")
    pages: list[str] = []
    for match in _STREAM_PATTERN.finditer(data):
        body = match.group("body")
        prefix = match.group("prefix")
        stream = _bounded_flate(body) if b"/FlateDecode" in prefix else body
        text = _stream_text(stream)
        if text:
            pages.append(text)
    if not pages:
        raise SourceProviderError("the PDF does not contain extractable text")
    extracted = 0
    result: list[ExtractedPage] = []
    for number, text in enumerate(pages[:page_count], start=1):
        extracted += len(text)
        if extracted > MAX_EXTRACTED_CHARACTERS:
            raise SourceProviderError("extracted text exceeds the ingestion limit")
        result.append(ExtractedPage(text=text, page=number))
    return tuple(result)


def __getattr__(name: str) -> object:
    """Expose repository providers lazily without a provider-module import cycle."""
    if name in {"GitHubSourceProvider", "GitSourceProvider"}:
        from app.research.repository import GitHubSourceProvider, GitSourceProvider

        providers = {
            "GitHubSourceProvider": GitHubSourceProvider,
            "GitSourceProvider": GitSourceProvider,
        }
        return providers[name]
    raise AttributeError(name)


__all__ = [
    "APPROVED_URL_CONTENT_TYPES",
    "MAX_INGESTION_BYTES",
    "MAX_URL_REDIRECTS",
    "MAX_URL_RESPONSE_BYTES",
    "ExtractedPage",
    "ExtractedResearchDocument",
    "FileSourceProvider",
    "IngestionPayload",
    "ManualSourceProvider",
    "SourceProvider",
    "SourceProviderError",
    "URLSourceProvider",
]
