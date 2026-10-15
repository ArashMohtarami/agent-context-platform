"""Authlib Code+PKCE and JOSE validation, behind installation-pinned egress."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from authlib.integrations.httpx_client import AsyncOAuth2Client  # type: ignore[import-untyped]
from authlib.oidc.core import CodeIDToken  # type: ignore[import-untyped]
from joserfc import jwk, jwt
from joserfc._keys import KeySetSerialization

from app.identity.crypto import AuthBusy, AuthRejected
from app.identity.providers import Registration
from app.models.common import utc_now

type Row = dict[str, Any]


def validate_id_token(
    raw: str, keys: Row, registration: Registration, nonce: str, access_token: str
) -> Row:
    """Algorithms/keys and claim validation are maintained library implementations."""
    try:
        if not isinstance(raw, str) or not 1 <= len(raw) <= 32768:
            raise ValueError("invalid token size")
        if not isinstance(keys.get("keys"), list) or not 1 <= len(keys["keys"]) <= 20:
            raise ValueError("invalid key set")
        decoded = jwt.decode(
            raw,
            jwk.KeySet.import_key_set(cast(KeySetSerialization, keys)),
            algorithms=registration.algorithms,
        )
        claims = CodeIDToken(
            decoded.claims,
            decoded.header,
            options={
                "iss": {"essential": True, "value": registration.issuer},
                "aud": {"essential": True, "value": registration.client_id},
                "auth_time": {"essential": True},
            },
            params={
                "nonce": nonce,
                "client_id": registration.client_id,
                "access_token": access_token,
            },
        )
        claims.validate(now=int(utc_now().timestamp()), leeway=0)
        if claims.get("azp", registration.client_id) != registration.client_id:
            raise ValueError("invalid authorized party")
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
            raise ValueError("invalid subject")
        now = utc_now().timestamp()
        if not all(type(claims.get(k)) is int for k in ("exp", "iat", "auth_time")):
            raise ValueError("invalid timestamps")
        if not now - 120 <= claims["auth_time"] <= now + 5 or claims["iat"] > now + 5:
            raise ValueError("fresh provider authentication required")
        amr = claims.get("amr", [])
        if (
            not isinstance(amr, list)
            or len(amr) > 20
            or any(not isinstance(x, str) or not 1 <= len(x) <= 128 for x in amr)
        ):
            raise ValueError("invalid assurance")
        acr = claims.get("acr")
        if acr is not None and (not isinstance(acr, str) or not 1 <= len(acr) <= 128):
            raise ValueError("invalid assurance")
        return dict(claims)
    except Exception:
        raise AuthRejected("provider authentication was not accepted") from None


def provider_mfa(registration: Registration, claims: Row) -> bool:
    """Only an explicitly trusted rule qualifies. Roles/groups/email are ignored."""
    if not (registration.mfa_acr or registration.mfa_amr):
        return False
    return (not registration.mfa_acr or claims.get("acr") in registration.mfa_acr) and set(
        registration.mfa_amr
    ) <= set(claims.get("amr", []))


def authentication(registration: Registration, claims: Row, identity_id: str) -> Row:
    now = utc_now()
    return {
        "method": "oidc",
        "identity_id": identity_id,
        "registration_id": registration.registration_id,
        "registration_version": registration.version,
        "issuer": registration.issuer,
        "client_id": registration.client_id,
        "auth_time": datetime.fromtimestamp(claims["auth_time"], UTC),
        "valid_until": min(
            datetime.fromtimestamp(claims["exp"], UTC),
            now + timedelta(seconds=registration.max_session_seconds),
        ),
        "assurance_version": registration.assurance_version,
        "acr": claims.get("acr"),
        "verified_amr": list(dict.fromkeys(claims.get("amr", []))),
    }


class OIDCClient:
    def __init__(self) -> None:
        self.active = 0

    @staticmethod
    def client(registration: Registration) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            registration.client_id,
            registration.client_secret.get_secret_value(),
            token_endpoint_auth_method="client_secret_basic",  # noqa: S106 - protocol method name
            scope="openid",
            redirect_uri=registration.redirect_uri,
            code_challenge_method="S256",
            transport=registration.transport(),
            trust_env=False,
            follow_redirects=False,
            timeout=5,
        )

    async def discovery(self, client: AsyncOAuth2Client, registration: Registration) -> Row:
        response = await client.request("GET", registration.discovery_url, withhold_token=True)
        metadata: Row = response.json()
        if (
            metadata.get("issuer") != registration.issuer
            or any(
                metadata.get(key) != getattr(registration, key)
                for key in (
                    "authorization_endpoint",
                    "token_endpoint",
                    "jwks_uri",
                    "end_session_endpoint",
                )
            )
            or "S256" not in metadata.get("code_challenge_methods_supported", [])
            or "code" not in metadata.get("response_types_supported", [])
            or "authorization_code" not in metadata.get("grant_types_supported", [])
            or "client_secret_basic"
            not in metadata.get("token_endpoint_auth_methods_supported", [])
            or not set(registration.algorithms)
            <= set(metadata.get("id_token_signing_alg_values_supported", []))
        ):
            raise AuthRejected("provider discovery was not accepted")
        return metadata

    async def start(self, registration: Registration, state: str, nonce: str, verifier: str) -> str:
        async def work() -> str:
            async with self.client(registration) as client:
                await self.discovery(client, registration)
                url, _ = client.create_authorization_url(
                    registration.authorization_endpoint,
                    state=state,
                    nonce=nonce,
                    code_verifier=verifier,
                    max_age=0,
                    prompt="login",
                    response_mode="query",
                )
                return str(url)

        return await self.bounded(work)

    async def exchange(
        self, registration: Registration, code: str, nonce: str, verifier: str
    ) -> Row:
        async def work() -> Row:
            async with self.client(registration) as client:
                await self.discovery(client, registration)
                token = await client.fetch_token(
                    registration.token_endpoint,
                    code=code,
                    code_verifier=verifier,
                    grant_type="authorization_code",
                )
                keys = (
                    await client.request("GET", registration.jwks_uri, withhold_token=True)
                ).json()
                # Tokens are deliberately not persisted or returned to the browser.
                return validate_id_token(
                    token.get("id_token", ""),
                    keys,
                    registration,
                    nonce,
                    token.get("access_token", ""),
                )

        return await self.bounded(work)

    async def bounded[T](self, work: Callable[[], Awaitable[T]]) -> T:
        if self.active >= 4:
            raise AuthBusy("provider authentication is temporarily busy")
        self.active += 1
        try:
            async with asyncio.timeout(12):
                return await work()
        except (AuthRejected, AuthBusy):
            raise
        except Exception:
            raise AuthRejected("provider authentication was not accepted") from None
        finally:
            self.active -= 1
