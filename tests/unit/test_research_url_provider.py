"""V3 Phase 4 URL acquisition security and provenance tests."""

from collections.abc import AsyncIterator, Callable
from gzip import compress
from hashlib import sha256

import httpx
import pytest

from app.models.research import ResearchAuthorization, ResearchSourceDocument
from app.research.providers import (
    MAX_URL_RESPONSE_BYTES,
    IngestionPayload,
    SourceProviderError,
    URLSourceProvider,
)
from app.services.research_ingestion_service import ResearchIngestionService
from tests.unit.test_research_ingestion_service import Store

PROJECT = "payments-api"
RESEARCH = "payment-evidence"
SOURCE = "public-retry-guide"
PUBLIC_IP = "93.184.216.34"


class ByteStream(httpx.AsyncByteStream):
    """A one-shot response body so MockTransport follows the streaming path."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._content

    async def aclose(self) -> None:
        return None


def streamed_response(status: int, *, headers: dict[str, str], content: bytes) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=ByteStream(content))


def research_source(uri: str = "https://public.example/retries") -> ResearchSourceDocument:
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=SOURCE,
        source_type="url",
        uri=uri,
        canonical_uri=uri,
        title="Registered retry guide",
        source_class="official_documentation",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )


def payload() -> IngestionPayload:
    return IngestionPayload(
        data=b"",
        content_type="application/octet-stream",
        title="Registered retry guide",
        language="und",
        acquire_url=True,
    )


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    addresses: tuple[str, ...] = (PUBLIC_IP,),
) -> URLSourceProvider:
    async def resolve(host: str, port: int) -> tuple[str, ...]:
        assert host
        assert port in {80, 443}
        return addresses

    return URLSourceProvider(
        resolver=resolve,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_public_html_fixture_normalizes_inert_content_and_preserves_provenance() -> None:
    body = b"""<!doctype html>
    <html><head>
      <title>Retries &amp; idempotency</title>
      <meta name="author" content="Ada Lovelace">
      <meta property="og:site_name" content="Example Engineering">
      <meta property="article:published_time" content="2026-09-29T10:00:00Z">
      <link rel="canonical" href="/guides/retries">
      <script>window.neverRun = true</script>
    </head><body>
      <h1 id="overview">Retry overview</h1>
      <p>Use one stable key for every retry.</p>
      <h2 id="limits">Limits</h2><p>Back off before retrying.</p>
      <script>document.write('unsafe')</script>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        return streamed_response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"v2"',
                "last-modified": "Mon, 29 Sep 2026 10:00:00 GMT",
            },
            content=body,
        )

    extracted = await provider(handler).extract(research_source(), payload())

    assert extracted.content_type == "text/markdown"
    assert extracted.title == "Retries & idempotency"
    assert "# Retry overview" in extracted.content
    assert "window.neverRun" not in extracted.content
    assert extracted.source_hash == sha256(body).hexdigest()
    assert extracted.locator_url == "https://public.example/retries"
    assert extracted.version_identifier == 'etag:"v2"'
    assert extracted.snapshot_metadata == {
        "source_content_type": "text/html",
        "canonical_url": "https://public.example/retries",
        "retrieved_url": "https://public.example/retries",
        "title": "Retries & idempotency",
        "publisher": "Example Engineering",
        "redirect_count": 0,
        "declared_canonical_url": "https://public.example/guides/retries",
        "author": "Ada Lovelace",
        "published_at": "2026-09-29T10:00:00+00:00",
        "etag": '"v2"',
        "last_modified": "Mon, 29 Sep 2026 10:00:00 GMT",
    }
    assert extracted.pages[0].heading_anchors == ((1, "overview"), (3, "limits"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uri", "addresses", "message"),
    [
        ("http://localhost/private", (PUBLIC_IP,), "host is not permitted"),
        ("http://127.0.0.1/private", ("127.0.0.1",), "forbidden address"),
        ("http://[::1]/private", ("::1",), "forbidden address"),
        ("http://10.0.0.8/private", ("10.0.0.8",), "forbidden address"),
        (
            "http://169.254.169.254/latest/meta-data",
            ("169.254.169.254",),
            "forbidden address",
        ),
        (
            "http://metadata.google.internal/computeMetadata/v1",
            (PUBLIC_IP,),
            "host is not permitted",
        ),
    ],
)
async def test_private_and_cloud_metadata_targets_are_rejected_before_any_request(
    uri: str, addresses: tuple[str, ...], message: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return streamed_response(200, headers={"content-type": "text/html"}, content=b"<p>x</p>")

    with pytest.raises(SourceProviderError, match=message):
        await provider(handler, addresses=addresses).extract(research_source(uri), payload())

    assert requests == []


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_rejected_before_following_it() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with pytest.raises(SourceProviderError, match="forbidden address"):
        await provider(handler).extract(research_source(), payload())

    assert requests == ["https://public.example/retries"]


@pytest.mark.asyncio
async def test_redirect_limit_and_unbounded_stream_are_rejected() -> None:
    requests: list[str] = []

    def too_many_redirects(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": f"/hop-{len(requests)}"})

    with pytest.raises(SourceProviderError, match="redirect limit"):
        await provider(too_many_redirects).extract(research_source(), payload())
    assert len(requests) == 4

    def unbounded(_: httpx.Request) -> httpx.Response:
        return streamed_response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * (MAX_URL_RESPONSE_BYTES + 1),
        )

    with pytest.raises(SourceProviderError, match="size limit"):
        await provider(unbounded).extract(research_source(), payload())


@pytest.mark.asyncio
async def test_oversized_and_compressed_responses_are_rejected_without_normalization() -> None:
    def too_large(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-length": str(MAX_URL_RESPONSE_BYTES + 1),
            },
        )

    with pytest.raises(SourceProviderError, match="size limit"):
        await provider(too_large).extract(research_source(), payload())

    def compressed(_: httpx.Request) -> httpx.Response:
        return streamed_response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            content=compress(b"compressed content"),
        )

    with pytest.raises(SourceProviderError, match="compressed"):
        await provider(compressed).extract(research_source(), payload())


@pytest.mark.asyncio
async def test_unsupported_scheme_and_content_type_are_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return streamed_response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"
        )

    with pytest.raises(SourceProviderError, match="only http and https"):
        await provider(handler).extract(research_source("file:///etc/passwd"), payload())
    with pytest.raises(SourceProviderError, match="not approved"):
        await provider(handler).extract(research_source(), payload())


@pytest.mark.asyncio
async def test_duplicate_url_snapshot_is_idempotent_and_preserves_heading_citations() -> None:
    body = (
        b"<html><head><title>Retry guide</title></head><body><h1 id='keys'>Keys</h1>"
        b"<p>Use a stable retry key.</p></body></html>"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return streamed_response(
            200,
            headers={"content-type": "text/html", "etag": '"one"'},
            content=body,
        )

    store = Store()
    ingestion = ResearchIngestionService(
        store.snapshots_for,
        store.documents_for,
        store.chunks_for,
        store.jobs_for,
        None,
        url_provider=provider(handler),
    )
    first = await ingestion.ingest(
        source=research_source(), payload=payload(), manually_provided=False
    )
    second = await ingestion.ingest(
        source=research_source(), payload=payload(), manually_provided=False
    )

    assert first.idempotent is False
    assert second.idempotent is True
    assert len(store.snapshots) == len(store.documents) == len(store.chunks) == len(store.jobs) == 1
    snapshot = next(iter(store.snapshots.values()))
    chunk = next(iter(store.chunks.values()))
    assert snapshot.metadata["etag"] == '"one"'
    assert snapshot.version_identifier == 'etag:"one"'
    assert chunk.locator.url == "https://public.example/retries"
    assert chunk.locator.anchor == "keys"
    assert chunk.locator.path is None


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing domain behavior uses an explicit test organization."""
    yield
