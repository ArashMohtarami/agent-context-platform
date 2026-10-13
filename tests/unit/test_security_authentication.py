"""Turning a presented credential into a caller, or refusing to.

Every way a key can fail has its own test, because each is a different bug if
it stops working — and they all have to produce the same answer to the caller,
which is a property no single one of them shows.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from app.security.keys import PREFIX, mint, parse
from app.security.principal import UNAUTHENTICATED, AuthenticationError, unauthenticated_principal
from app.security.scopes import ALL_SCOPES
from app.services.auth_service import REFUSED, AuthService, granted_scopes, principal_of
from tests.security_doubles import NOW, FakeApiKeyRepository, expired_key, issued_key

PROJECT = "payment-service"


def service(
    *records: object,
    clock: Callable[[], datetime] | None = None,
    record_usage: bool = True,
) -> tuple[AuthService, FakeApiKeyRepository]:
    """Build an authentication service over the given stored keys."""
    keys = FakeApiKeyRepository(*records)  # type: ignore[arg-type]
    return (
        AuthService(keys, clock=clock or (lambda: NOW), record_usage=record_usage),
        keys,
    )


async def refuses(auth: AuthService, credential: str | None) -> str:
    """Assert one credential is refused, and return what the caller was told."""
    with pytest.raises(AuthenticationError) as failure:
        await auth.authenticate(credential)
    return str(failure.value)


@pytest.mark.asyncio
async def test_a_valid_key_authenticates_as_what_it_was_granted() -> None:
    record, token = issued_key(scopes=("search:read", "context:read"), projects=(PROJECT,))
    auth, _ = service(record)

    principal = await auth.authenticate(token)

    assert principal.key_id == record.key_id
    assert principal.name == record.name
    assert principal.scopes == ("context:read", "search:read")
    assert principal.projects == (PROJECT,)
    assert principal.authenticated is True


@pytest.mark.asyncio
async def test_a_missing_credential_is_refused() -> None:
    record, _ = issued_key()
    auth, _ = service(record)

    assert await refuses(auth, None) == REFUSED
    assert await refuses(auth, "") == REFUSED


@pytest.mark.asyncio
async def test_a_credential_that_is_not_a_key_is_refused_without_a_lookup() -> None:
    record, _ = issued_key()
    auth, keys = service(record)
    seen: list[str] = []
    original = keys.get_by_key_id

    async def watched(key_id: str) -> object:
        seen.append(key_id)
        return await original(key_id)

    keys.get_by_key_id = watched  # type: ignore[method-assign]

    assert await refuses(auth, "not-a-key") == REFUSED
    # Nothing a caller sends becomes a lookup value until it is shaped like a
    # key, so a crafted header cannot reach the database at all.
    assert seen == []


@pytest.mark.asyncio
async def test_an_unknown_key_is_refused() -> None:
    auth, _ = service()

    assert await refuses(auth, mint().token) == REFUSED


@pytest.mark.asyncio
async def test_the_right_identifier_with_the_wrong_secret_is_refused() -> None:
    record, _ = issued_key()
    auth, _ = service(record)
    forged = f"{PREFIX}_{record.key_id}_{parse(mint().token).secret}"

    assert await refuses(auth, forged) == REFUSED


@pytest.mark.asyncio
async def test_a_revoked_key_is_refused() -> None:
    record, token = issued_key(status="revoked")
    auth, _ = service(record)

    assert await refuses(auth, token) == REFUSED


@pytest.mark.asyncio
async def test_an_expired_key_is_refused() -> None:
    record, token = expired_key()
    auth, _ = service(record)

    assert await refuses(auth, token) == REFUSED


@pytest.mark.asyncio
async def test_a_key_is_refused_the_moment_it_expires_not_after() -> None:
    record, token = issued_key(expires_in=timedelta(hours=1))
    before, _ = service(record, clock=lambda: NOW + timedelta(minutes=59))
    at, _ = service(record, clock=lambda: record.expires_at or NOW)

    assert (await before.authenticate(token)).key_id == record.key_id
    assert await refuses(at, token) == REFUSED


@pytest.mark.asyncio
async def test_a_key_with_no_expiry_keeps_working() -> None:
    record, token = issued_key(expires_in=None)
    auth, _ = service(record, clock=lambda: NOW + timedelta(days=3_650))

    assert (await auth.authenticate(token)).key_id == record.key_id


@pytest.mark.asyncio
async def test_every_refusal_says_the_same_thing() -> None:
    unknown, _ = service()
    revoked_record, revoked_token = issued_key(status="revoked")
    expired_record, expired_token = expired_key()
    valid_record, _ = issued_key()
    forged = f"{PREFIX}_{valid_record.key_id}_{parse(mint().token).secret}"

    answers = {
        await refuses(unknown, None),
        await refuses(unknown, "garbage"),
        await refuses(unknown, mint().token),
        await refuses(service(revoked_record)[0], revoked_token),
        await refuses(service(expired_record)[0], expired_token),
        await refuses(service(valid_record)[0], forged),
    }

    # One answer for six different reasons. Distinguishing them would tell
    # someone probing which keys exist and which merely expired.
    assert answers == {REFUSED}


@pytest.mark.asyncio
async def test_a_refusal_never_names_a_secret_or_a_hash() -> None:
    record, token = issued_key(status="revoked")
    auth, _ = service(record)

    message = await refuses(auth, token)

    assert parse(token).secret not in message
    assert record.key_hash not in message
    assert record.key_id not in message


@pytest.mark.asyncio
async def test_the_operator_log_keeps_the_reason_the_caller_is_not_told(
    caplog: pytest.LogCaptureFixture,
) -> None:
    record, token = expired_key()
    auth, _ = service(record)

    with caplog.at_level(logging.WARNING, logger="agent_context.security"):
        await refuses(auth, token)

    # The distinction that is withheld from the caller is exactly the one an
    # operator needs, so it is kept where only they can read it. Asserted on
    # the emitted record rather than the rendered line, because the fields are
    # what the JSON formatter ships.
    emitted = caplog.records[-1]
    assert emitted.message == "authentication_refused"
    assert emitted.auth_reason == "expired"  # type: ignore[attr-defined]
    assert emitted.key_id == record.key_id  # type: ignore[attr-defined]
    # And never the credential itself.
    assert parse(token).secret not in caplog.text
    assert record.key_hash not in caplog.text


@pytest.mark.asyncio
async def test_using_a_key_records_when_it_was_used() -> None:
    record, token = issued_key()
    auth, keys = service(record)

    await auth.authenticate(token)

    assert keys.touched == [(record.key_id, NOW)]
    assert keys.records[record.key_id].last_used_at == NOW


@pytest.mark.asyncio
async def test_a_refused_key_is_not_recorded_as_used() -> None:
    record, token = issued_key(status="revoked")
    auth, keys = service(record)

    await refuses(auth, token)

    assert keys.touched == []


@pytest.mark.asyncio
async def test_usage_recording_can_be_switched_off() -> None:
    record, token = issued_key()
    auth, keys = service(record, record_usage=False)

    await auth.authenticate(token)

    assert keys.touched == []


def test_an_open_deployments_caller_holds_everything_and_says_it_is_not_authenticated() -> None:
    principal = unauthenticated_principal()

    assert principal.scopes == ALL_SCOPES
    assert principal.projects is None
    assert principal.authenticated is False
    # Named so a log line can never confuse it for a real key.
    assert principal.key_id == UNAUTHENTICATED


def test_a_stored_key_describes_the_caller_it_authorizes() -> None:
    record, _ = issued_key(scopes=("memory:write", "search:read"), projects=(PROJECT,))

    principal = principal_of(record)

    assert granted_scopes(record) == ("memory:write", "search:read")
    assert principal.projects == (PROJECT,)


def test_a_key_naming_no_project_reaches_every_project() -> None:
    record, _ = issued_key(projects=())

    principal = principal_of(record)

    # Stored empty because that is what omitting the option at creation says,
    # and read back as the widest grant rather than as no grant at all.
    assert principal.projects is None
    assert principal.allows_project(PROJECT)


def test_a_principal_describes_itself_without_a_credential() -> None:
    record, token = issued_key(scopes=("search:read",), projects=(PROJECT,))

    described = principal_of(record).describe()

    assert described == {
        "key_id": record.key_id,
        "scopes": ["search:read"],
        "projects": [PROJECT],
        "profiles": "all",
    }
    assert record.key_hash not in str(described)
    assert parse(token).secret not in str(described)
