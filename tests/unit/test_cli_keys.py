"""Issuing, listing, and revoking keys from the terminal.

There is no endpoint for minting a key, so this is the only way one comes into
existence — which makes what it prints, and what it refuses, part of the
security surface rather than a convenience.
"""

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.cli import contextctl
from app.cli.commands import keys as keys_command
from app.cli.errors import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, EXIT_USAGE
from app.db.client import MongoDBNotReadyError
from app.security.keys import PREFIX, hash_secret, parse
from app.security.scopes import ALL_SCOPES, REQUIRED_SCOPES
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, issued_key

PROJECT = "payment-service"


@pytest.fixture(autouse=True)
def platform_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Configure the database settings the key commands read."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    monkeypatch.setenv("MONGODB_URI", "mongodb://mongodb:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "agent_context_test")
    monkeypatch.setenv("MONGODB_USERNAME", "application_user")
    monkeypatch.setenv("MONGODB_PASSWORD_FILE", str(password_file))
    monkeypatch.delenv("MONGODB_PASSWORD", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(contextctl, "get_settings", get_settings)


class _Platform:
    """Stand in for the MongoDB connection the key commands open."""

    def __init__(self, keys: FakeApiKeyRepository, audit: FakeAuditRepository) -> None:
        self.keys = keys
        self.audit = audit
        self.closed = False
        self.required_search: bool | None = None


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Platform]:
    """Run the key commands against in-memory collections."""
    state = _Platform(FakeApiKeyRepository(), FakeAuditRepository())

    class FakeClient:
        def __init__(self, settings: Any) -> None:
            self.database = object()

        async def connect(self, *, require_search: bool = True) -> None:
            state.required_search = require_search

        async def close(self) -> None:
            state.closed = True

    monkeypatch.setattr(keys_command, "MongoDBClient", FakeClient)
    monkeypatch.setattr(keys_command, "MongoApiKeyRepository", lambda database: state.keys)
    monkeypatch.setattr(keys_command, "MongoAuditRepository", lambda database: state.audit)
    yield state


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run one invocation and return its exit code and streams."""
    code = contextctl.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def create(capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str, str]:
    """Issue one key with the smallest command line that works."""
    return run(capsys, "keys", "create", "--name", "codex", "--scope", "search:read", *extra)


def test_a_created_key_is_printed_once_and_stored_only_as_a_hash(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = create(capsys)

    assert code == EXIT_OK
    stored = next(iter(platform.keys.records.values()))
    token = out.rsplit("key: ", 1)[1].strip()
    # What was printed authenticates; what was kept cannot be turned back into
    # it, and does not appear in the record under any other name.
    presented = parse(token)
    assert token.startswith(f"{PREFIX}_{stored.key_id}_")
    assert presented.key_id == stored.key_id
    assert stored.key_hash == hash_secret(presented.secret)
    assert token not in stored.model_dump_json()
    assert stored.key_hash not in out


def test_the_key_is_carried_by_json_so_it_can_be_piped_into_a_secret_store(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "--json", "keys", "create", "--name", "ci", "--scope", "search:read")

    payload = json.loads(out)
    assert code == EXIT_OK
    assert payload["key"].startswith(f"{PREFIX}_")
    # The hash is the platform's business, not the operator's.
    assert "key_hash" not in payload


def test_a_created_key_carries_the_scopes_and_projects_it_was_given(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = create(
        capsys, "--scope", "context:write", "--project", PROJECT, "--expires-in-days", "90"
    )

    stored = next(iter(platform.keys.records.values()))
    assert code == EXIT_OK
    # Canonical order, not the order they were typed: one definition of a
    # grant, however it was written.
    assert stored.scopes == ["context:write", "search:read"]
    assert stored.projects == [PROJECT]
    assert stored.expires_at is not None
    assert stored.expires_at - stored.created_at == timedelta(days=90)
    assert PROJECT in out


def test_naming_no_project_grants_every_project_and_says_so(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = create(capsys)

    stored = next(iter(platform.keys.records.values()))
    assert code == EXIT_OK
    # Stored empty, because that is what the widest grant looks like; rendered
    # as `all`, because an empty cell would read as a mistake.
    assert stored.projects == []
    assert "all" in out


def test_a_key_that_does_not_expire_is_possible_but_deliberate(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = create(capsys)

    assert code == EXIT_OK
    assert next(iter(platform.keys.records.values())).expires_at is None


@pytest.mark.parametrize("days", ["0", "-1"])
def test_a_key_that_would_be_born_expired_is_refused(
    platform: _Platform, capsys: pytest.CaptureFixture[str], days: str
) -> None:
    code, _, err = create(capsys, "--expires-in-days", days)

    assert code == EXIT_USAGE
    assert "at least one day" in err
    assert platform.keys.records == {}


def test_an_unknown_scope_is_refused_before_anything_is_written(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "keys", "create", "--name", "n", "--scope", "search:write")

    # argparse refuses it against the known list, so a typo never reaches the
    # service and never mints a key that grants nothing.
    assert code == EXIT_USAGE
    assert "search:write" in err
    assert platform.keys.records == {}


def test_a_key_granting_nothing_cannot_be_created(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = run(capsys, "keys", "create", "--name", "n")

    assert code == EXIT_USAGE
    assert platform.keys.records == {}


def test_listing_shows_what_exists_without_showing_any_secret(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    record, token = issued_key(projects=(PROJECT,), name="payments agent")
    platform.keys.records[record.key_id] = record

    code, out, _ = run(capsys, "keys", "list")

    assert code == EXIT_OK
    assert record.key_id in out
    assert "payments agent" in out
    assert PROJECT in out
    assert token not in out
    assert record.key_hash not in out


def test_listing_can_be_narrowed_to_one_state(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    active, _ = issued_key(name="active key")
    revoked, _ = issued_key(name="revoked key", status="revoked")
    platform.keys.records = {active.key_id: active, revoked.key_id: revoked}

    code, out, _ = run(capsys, "keys", "list", "--status", "active")

    assert code == EXIT_OK
    assert "active key" in out
    assert "revoked key" not in out


def test_an_empty_listing_says_so(platform: _Platform, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "keys", "list")

    assert code == EXIT_OK
    assert "(none)" in out


def test_revoking_leaves_the_record_and_stops_the_key(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    record, _ = issued_key()
    platform.keys.records[record.key_id] = record

    code, out, _ = run(capsys, "keys", "revoke", "--key-id", record.key_id)

    assert code == EXIT_OK
    stored = platform.keys.records[record.key_id]
    assert stored.status == "revoked"
    assert not stored.is_usable()
    # Kept, so the audit trail still has a name to attach past decisions to.
    assert stored.name == record.name
    assert "revoked" in out


def test_revoking_the_same_key_twice_is_not_a_failure(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    record, _ = issued_key(status="revoked")
    platform.keys.records[record.key_id] = record

    code, _, _ = run(capsys, "keys", "revoke", "--key-id", record.key_id)

    assert code == EXIT_OK


def test_revoking_a_key_that_does_not_exist_fails_clearly(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "keys", "revoke", "--key-id", "ffffffffffffffff")

    assert code == EXIT_FAILED
    assert "no key" in err


def test_issuing_and_revoking_are_both_recorded(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    key_id = next(iter(platform.keys.records))
    run(capsys, "keys", "revoke", "--key-id", key_id)

    assert platform.audit.actions() == [("key.create", "allowed"), ("key.revoke", "allowed")]
    # The identifier is public and worth recording; the key is neither.
    assert all(PREFIX not in reason for reason in platform.audit.reasons())


def test_the_scopes_command_describes_every_scope_and_needs_no_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No `platform` fixture: this reads nothing, so it has to answer wherever
    # `contextctl` is installed rather than only where MongoDB is configured.
    code, out, _ = run(capsys, "keys", "scopes")

    assert code == EXIT_OK
    for scope in ALL_SCOPES:
        assert scope in out
    for scope in REQUIRED_SCOPES:
        assert scope in out


def test_an_unreachable_database_is_an_outage_not_a_command_failure(
    platform: _Platform, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class RefusingClient:
        def __init__(self, settings: Any) -> None:
            self.database = object()

        async def connect(self, *, require_search: bool = True) -> None:
            raise MongoDBNotReadyError("connection refused")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(keys_command, "MongoDBClient", RefusingClient)

    code, _, err = run(capsys, "keys", "list")

    assert code == EXIT_UNAVAILABLE
    assert "MongoDB" in err


def test_the_connection_is_closed_even_when_the_command_fails(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "keys", "revoke", "--key-id", "ffffffffffffffff")

    assert platform.closed is True


def test_issuing_a_key_does_not_require_search_to_be_working(
    platform: _Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = create(capsys)

    # A credential has nothing to do with retrieval. Requiring a healthy search
    # coordinator to mint one would mean authentication could not be
    # bootstrapped on a platform whose search is degraded — which is exactly
    # when an operator is most likely to need to.
    assert code == EXIT_OK
    assert platform.required_search is False
