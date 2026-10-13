"""What the security documents refuse to be.

A stored key or audit record is the last place a bad value can be caught before
it becomes something an authorization decision is made from, so the bounds are
on the model rather than at whichever caller happened to write it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models.api_key import MAX_PROJECTS, MAX_SCOPES, ApiKeyDocument
from app.models.audit_log import MAX_REASON, AuditLogDocument
from app.security.keys import mint

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def key(**overrides: object) -> ApiKeyDocument:
    """Build one stored key, overriding whatever is under test."""
    minted = mint()
    values: dict[str, object] = {
        "key_id": minted.key_id,
        "key_hash": minted.key_hash,
        "name": "test key",
        "scopes": ["search:read"],
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ApiKeyDocument(**values)  # type: ignore[arg-type]


def test_a_well_formed_key_is_accepted() -> None:
    record = key()

    assert record.status == "active"
    assert record.projects == []
    assert record.expires_at is None
    assert record.last_used_at is None


@pytest.mark.parametrize(
    "key_id",
    ["", "short", "0123456789abcdef0", "0123456789ABCDEF", "0123456789abcdeg", "0123456789abcde "],
)
def test_an_identifier_that_is_not_sixteen_hex_characters_is_refused(key_id: str) -> None:
    # This is what a lookup filters on, so its shape is what stops a caller's
    # text from becoming part of a query.
    with pytest.raises(ValidationError):
        key(key_id=key_id)


@pytest.mark.parametrize("key_hash", ["", "abc", "z" * 64, "a" * 63, "a" * 65, "A" * 64])
def test_a_hash_that_is_not_a_sha256_digest_is_refused(key_hash: str) -> None:
    with pytest.raises(ValidationError):
        key(key_hash=key_hash)


def test_a_key_granting_nothing_is_refused() -> None:
    # A key that permits nothing is a mistake, not a very safe key.
    with pytest.raises(ValidationError):
        key(scopes=[])


def test_an_unknown_scope_is_refused_rather_than_stored() -> None:
    with pytest.raises(ValidationError, match="unknown scopes"):
        key(scopes=["search:read", "everything"])


def test_a_repeated_scope_or_project_is_refused() -> None:
    with pytest.raises(ValidationError, match="unique"):
        key(scopes=["search:read", "search:read"])
    with pytest.raises(ValidationError, match="unique"):
        key(projects=["payment-service", "payment-service"])


def test_a_grant_is_bounded_in_size() -> None:
    # An unbounded allow-list is an unbounded document, and this one is read on
    # every authenticated request.
    with pytest.raises(ValidationError):
        key(scopes=[f"scope-{index}" for index in range(MAX_SCOPES + 1)])
    with pytest.raises(ValidationError):
        key(projects=[f"project-{index}" for index in range(MAX_PROJECTS + 1)])


def test_a_key_that_expired_before_it_was_issued_is_refused() -> None:
    with pytest.raises(ValidationError, match="after created_at"):
        key(expires_at=NOW - timedelta(days=1))
    with pytest.raises(ValidationError, match="after created_at"):
        key(expires_at=NOW)


def test_expiry_is_a_question_about_a_moment() -> None:
    record = key(expires_at=NOW + timedelta(days=1))

    assert record.is_expired(NOW) is False
    assert record.is_expired(NOW + timedelta(days=2)) is True
    # The boundary belongs to the expired side: a key is not usable at the
    # instant it expires.
    assert record.is_expired(NOW + timedelta(days=1)) is True


def test_usability_is_expiry_and_status_together() -> None:
    assert key().is_usable(NOW) is True
    assert key(status="revoked").is_usable(NOW) is False
    assert key(expires_at=NOW + timedelta(days=1)).is_usable(NOW + timedelta(days=2)) is False


def test_a_timestamp_is_stored_at_the_precision_it_reads_back_at() -> None:
    # MongoDB keeps milliseconds. Truncating here too means an expiry compares
    # against the same instant before and after a round trip.
    record = key(expires_at=datetime(2026, 9, 2, 12, 0, 0, 123_456, tzinfo=UTC))

    assert record.expires_at is not None
    assert record.expires_at.microsecond == 123_000


def test_an_audit_record_keeps_the_route_without_the_query_string() -> None:
    # A path is a route; a query string is a caller's data, and a search query
    # is the one field that can describe the person who ran it.
    with pytest.raises(ValidationError, match="query string"):
        AuditLogDocument(
            action="authorize",
            outcome="denied",
            key_id="0123456789abcdef",
            path="/api/v1/search?query=the+production+password",
        )


def test_an_audit_reason_is_bounded() -> None:
    with pytest.raises(ValidationError):
        AuditLogDocument(
            action="authorize",
            outcome="denied",
            key_id="0123456789abcdef",
            reason="x" * (MAX_REASON + 1),
        )


def test_an_audit_record_names_a_caller_even_without_a_key() -> None:
    record = AuditLogDocument(action="authenticate", outcome="denied", key_id="unauthenticated")

    # Never a key identifier's shape, so it cannot be mistaken for one.
    assert record.key_id == "unauthenticated"
    assert record.key_name is None


@pytest.mark.parametrize("action", ["nonsense", "", "key.mint", "context.read"])
def test_an_action_outside_the_vocabulary_is_refused(action: str) -> None:
    with pytest.raises(ValidationError):
        AuditLogDocument(
            action=action,  # type: ignore[arg-type]
            outcome="allowed",
            key_id="0123456789abcdef",
        )


@pytest.mark.parametrize("outcome", ["ok", "", "success", "refused"])
def test_an_outcome_outside_the_vocabulary_is_refused(outcome: str) -> None:
    with pytest.raises(ValidationError):
        AuditLogDocument(
            action="authorize",
            outcome=outcome,  # type: ignore[arg-type]
            key_id="0123456789abcdef",
        )


def test_neither_document_accepts_a_field_nobody_defined() -> None:
    # `extra="forbid"`: a misspelled field is a refused write rather than a
    # value that silently never applies.
    with pytest.raises(ValidationError):
        key(secret="hunter2")
    with pytest.raises(ValidationError):
        AuditLogDocument(
            action="authorize",
            outcome="allowed",
            key_id="0123456789abcdef",
            content="what was retrieved",  # type: ignore[call-arg]
        )
