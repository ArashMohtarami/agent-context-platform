"""Installation-approved OIDC registrations. No discovery URL supplied by browsers."""

import hashlib
import ipaddress
import json
import ssl
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.identity.crypto import AuthRejected


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "%" in parsed.netloc
        or len(value) > 2048
    ):
        raise ValueError("an exact HTTPS endpoint is required")
    return value


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    org_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    issuer: str
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: SecretStr = Field(repr=False, min_length=32, max_length=4096)
    redirect_uri: str
    post_logout_redirect_uri: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str
    connect_ip: str
    ca_file: Path | None = None
    algorithms: list[str] = Field(default_factory=lambda: ["RS256"], min_length=1, max_length=3)
    # Empty rules grant no MFA assurance, regardless of claims named "mfa".
    mfa_acr: list[str] = Field(default_factory=list, max_length=10)
    mfa_amr: list[str] = Field(default_factory=list, max_length=10)
    require_mfa: bool = False
    assurance_version: int = Field(default=1, ge=1)
    max_session_seconds: int = Field(default=3600, ge=60, le=28800)

    @model_validator(mode="after")
    def endpoints(self) -> Self:
        issuer = urlsplit(https_url(self.issuer))
        for field in (
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "end_session_endpoint",
        ):
            endpoint = urlsplit(https_url(getattr(self, field)))
            if endpoint.netloc != issuer.netloc:
                raise ValueError("provider endpoints must share the approved issuer authority")
        https_url(self.redirect_uri)
        https_url(self.post_logout_redirect_uri)
        address = ipaddress.ip_address(self.connect_ip)
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("provider connection address must be explicitly routable")
        if not set(self.algorithms) <= {"RS256", "PS256", "ES256"}:
            raise ValueError("only approved asymmetric signature algorithms are supported")
        if self.require_mfa and not (self.mfa_acr or self.mfa_amr):
            raise ValueError("required provider MFA needs an explicit assurance rule")
        if any(
            not isinstance(x, str) or not 1 <= len(x) <= 128 for x in self.mfa_acr + self.mfa_amr
        ):
            raise ValueError("assurance rules must be bounded exact values")
        return self

    @property
    def discovery_url(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    @property
    def version(self) -> str:
        data = self.model_dump(mode="json")
        data["client_secret"] = self.client_secret.get_secret_value()
        if self.ca_file:
            if self.ca_file.stat().st_size > 262144:
                raise ValueError("approved provider CA file is too large")
            data["ca_sha256"] = hashlib.sha256(self.ca_file.read_bytes()).hexdigest()
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def transport(self) -> "PinnedTransport":
        return PinnedTransport(self)


class Registry:
    def __init__(self, registrations: list[Registration], origin: str) -> None:
        if not 1 <= len(registrations) <= 50:
            raise ValueError("a bounded list of provider registrations is required")
        self.registrations = {item.registration_id: item for item in registrations}
        if len(self.registrations) != len(registrations):
            raise ValueError("duplicate provider registration")
        for item in registrations:
            if item.redirect_uri != origin + "/api/v6/auth/oidc/callback":
                raise ValueError("OIDC redirect must exactly match the browser callback")
            if item.post_logout_redirect_uri != origin + "/api/v6/auth/oidc/logged-out":
                raise ValueError("logout redirect must exactly match the browser endpoint")

    @classmethod
    def from_file(cls, path: Path, origin: str) -> "Registry":
        try:
            if path.stat().st_size > 262144:
                raise ValueError("provider registration file is too large")
            data = json.loads(path.read_text())
            if not isinstance(data, list):
                raise ValueError("provider registrations must be a list")
            return cls([Registration.model_validate(row) for row in data], origin)
        except Exception:
            # Pydantic's diagnostic inputs can contain a malformed client secret.
            raise ValueError("approved provider registration file is invalid") from None

    def get(self, registration_id: str) -> Registration:
        if registration_id not in self.registrations:
            raise AuthRejected("authentication was not accepted")
        return self.registrations[registration_id]


class PinnedTransport(httpx.AsyncBaseTransport):
    """Exact egress URLs + pinned IP, original TLS SNI/Host, bounded response bytes.

    DNS is never consulted. The installation operator must approve address changes.
    No redirects, proxies, cookies, userinfo, dynamic registration or URL fetch API.
    """

    def __init__(self, registration: Registration) -> None:
        self.registration = registration
        self.allowed = {
            registration.discovery_url,
            registration.token_endpoint,
            registration.jwks_uri,
        }
        context = ssl.create_default_context(cafile=registration.ca_file)
        self.inner = httpx.AsyncHTTPTransport(
            verify=context, retries=0, limits=httpx.Limits(max_connections=4)
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) not in self.allowed or request.url.scheme != "https":
            raise AuthRejected("provider endpoint was not accepted")
        host = request.url.host
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode()
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=self.registration.connect_ip),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        response = await self.inner.handle_async_request(pinned)
        try:
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16384):
                content.extend(chunk)
                if len(content) > 131072:
                    raise AuthRejected("provider response was not accepted")
            if response.status_code != 200:
                raise AuthRejected("provider response was not accepted")
            response_headers = [
                (key, value)
                for key, value in response.headers.raw
                if key.lower() not in {b"content-encoding", b"content-length"}
            ]
            return httpx.Response(200, headers=response_headers, content=bytes(content))
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()
