"""Taking a backup, checking it, and expiring it.

The MongoDB tools are stood in for, because what is under test is the
sequencing around them: that a half-written archive never becomes a backup,
that a manifest records what a restore can be checked against, and that
verification fails when it should rather than reporting success.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.errors import OperationFailure

from app.backup import naming
from app.backup.manifest import digest, read, write
from app.backup.tools import DUMP, RESTORE, ToolFailedError, ToolNotFoundError, ToolResult
from app.core.config import Settings
from app.services.backup_service import (
    BackupError,
    BackupNotFoundError,
    BackupService,
    total_bytes,
)

NOW = datetime(2026, 9, 16, 13, 15, 32, tzinfo=UTC)
ARCHIVE_BYTES = b"pretend mongodump archive"


class FakeTools:
    """Stand in for mongodump and mongorestore.

    `run` writes the file mongodump would have written, so the service's own
    handling of partial files, permissions, and checksums is exercised for
    real rather than mocked away.
    """

    def __init__(
        self,
        *,
        fails: set[str] | None = None,
        content: bytes = ARCHIVE_BYTES,
        database: "FakeBackupRepository | None" = None,
    ) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._fails = fails or set()
        self._content = content
        self._database = database

    def connection_arguments(self, *, database: str | None = None) -> list[str]:
        """Return placeholder connection arguments."""
        arguments = ["--uri=mongodb://mongodb:27017", "--username=app"]
        if database is not None:
            arguments.append(f"--db={database}")
        return arguments

    async def run(self, tool: str, arguments: list[str]) -> ToolResult:
        """Record the call, write what the tool would have, or fail."""
        self.calls.append((tool, list(arguments)))
        if tool in self._fails:
            raise ToolFailedError(f"{tool} failed: pretend failure")
        if tool == DUMP:
            target = _archive_argument(arguments)
            target.write_bytes(self._content)
        if tool == RESTORE and self._database is not None:
            # A restore check remaps every collection under a prefix. Doing
            # that to the fake database is what makes the counting real.
            prefix = _remap_prefix(arguments)
            if prefix is not None:
                self._database.restore_into(prefix)
        return ToolResult(stdout="", stderr="")

    def arguments_for(self, tool: str) -> list[list[str]]:
        """Return every invocation of one tool."""
        return [arguments for name, arguments in self.calls if name == tool]


def _archive_argument(arguments: Sequence[str]) -> Path:
    """Return the path one `--archive=` argument names."""
    for argument in arguments:
        if argument.startswith("--archive="):
            return Path(argument.removeprefix("--archive="))
    raise AssertionError("no --archive argument")


def _remap_prefix(arguments: Sequence[str]) -> str | None:
    """Return the collection prefix one `--nsTo=` argument asks for."""
    for argument in arguments:
        if argument.startswith("--nsTo="):
            target = argument.removeprefix("--nsTo=")
            return target.split(".", 1)[1].removesuffix("*")
    return None


class FakeBackupRepository:
    """A repository whose isolated restore copies can actually be populated.

    Stateful on purpose: the restore check counts what arrived and drops it
    again, and both are worth exercising against something that changes.
    """

    def __init__(self, counts: dict[str, int], restores_as: dict[str, int] | None = None) -> None:
        self.counts = dict(counts)
        self.dropped: list[str] = []
        self._restores_as = dict(restores_as if restores_as is not None else counts)

    async def collection_counts(self) -> dict[str, int]:
        """Return the live collection counts."""
        return dict(self.counts)

    async def restored_document_count(self, prefix: str) -> int:
        """Count only the isolated collections made by one check."""
        return sum(count for name, count in self.counts.items() if name.startswith(prefix))

    async def discard_restored(self, prefix: str) -> None:
        """Remove one check's isolated collections."""
        for name in list(self.counts):
            if name.startswith(prefix):
                self.dropped.append(name)
                self.counts.pop(name)

    def restore_into(self, prefix: str) -> None:
        """Populate the collections a restore check's remap would create."""
        for name, count in self._restores_as.items():
            self.counts[f"{prefix}{name}"] = count


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    """Build settings whose backup directory is this test's own."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    values: dict[str, Any] = {
        "_env_file": None,
        "mongodb_uri": "mongodb://mongodb:27017",
        "mongodb_database": "agent_context_test",
        "mongodb_username": "application_test_user",
        "mongodb_password_file": password_file,
        "backup_directory": tmp_path / "backups",
    }
    values.update(overrides)
    return Settings(**values)


def fake_database(
    collections: dict[str, int] | None = None,
    *,
    restores_as: dict[str, int] | None = None,
) -> FakeBackupRepository:
    """Build a database double, and what a restore check would find in it.

    `restores_as` is what the restored copies report. Leaving it out means the
    restore was faithful, which is the normal case; setting it is how a restore
    that brought back less than was taken is expressed without a real
    mongorestore.
    """
    counts = collections if collections is not None else {"skills": 3, "memories": 5}
    return FakeBackupRepository(counts, restores_as=restores_as)


def service_for(
    tmp_path: Path,
    *,
    tools: FakeTools | None = None,
    database: FakeBackupRepository | None = None,
    now: datetime = NOW,
    **overrides: object,
) -> tuple[BackupService, FakeTools]:
    """Build a service over doubles."""
    built = tools if tools is not None else FakeTools(database=database)
    if built._database is None:
        built._database = database
    service = BackupService(
        settings_for(tmp_path, **overrides),
        built,  # type: ignore[arg-type]
        repository=database,
        clock=lambda: now,
        nonce=lambda: "nonce",
    )
    return service, built


# --- creating --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_backup_lands_under_a_name_that_carries_its_instant(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path)

    stored = await service.create()

    assert stored.name == naming.archive_name(NOW)
    assert stored.archive.read_bytes() == ARCHIVE_BYTES
    assert naming.taken_at(stored.name) == NOW


@pytest.mark.asyncio
async def test_the_backup_directory_is_created_private(tmp_path: Path) -> None:
    # An archive is every document the deployment holds, in one file.
    service, _ = service_for(tmp_path)

    stored = await service.create()

    assert service.directory.stat().st_mode & 0o777 == 0o700
    assert stored.archive.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_an_existing_backup_directory_is_tightened_to_private(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path)
    service.directory.mkdir(parents=True, mode=0o755)
    service.directory.chmod(0o755)

    await service.create()

    assert service.directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_a_manifest_records_what_a_restore_can_be_checked_against(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path, database=fake_database({"skills": 12, "memories": 40}))

    stored = await service.create()

    manifest = read(service.directory / naming.manifest_name(stored.name))
    assert manifest.archive == stored.name
    assert manifest.database == "agent_context_test"
    assert manifest.collections == {"memories": 40, "skills": 12}
    assert manifest.documents == 52
    assert manifest.sha256 == digest(stored.archive)
    assert manifest.bytes == len(ARCHIVE_BYTES)


@pytest.mark.asyncio
async def test_a_backup_is_taken_of_the_application_database_compressed(tmp_path: Path) -> None:
    service, tools = service_for(tmp_path)

    await service.create()

    arguments = tools.arguments_for(DUMP)[0]
    assert "--db=agent_context_test" in arguments
    assert "--gzip" in arguments


@pytest.mark.asyncio
async def test_an_interrupted_backup_never_becomes_one(tmp_path: Path) -> None:
    # The archive is written under a partial name and moved into place only
    # once complete, so a restore never chooses a half-written file.
    service, _ = service_for(tmp_path, tools=FakeTools(fails={DUMP}))

    with pytest.raises(BackupError):
        await service.create()

    assert service.stored() == ()
    assert list(service.directory.glob("*")) == []


@pytest.mark.asyncio
async def test_a_backup_without_a_database_still_backs_up(tmp_path: Path) -> None:
    # The counts make a restore checkable; their absence is not a reason to
    # refuse to take a backup at all.
    service, _ = service_for(tmp_path, database=None)

    stored = await service.create()

    assert stored.manifest is not None
    assert stored.manifest.collections == {}


@pytest.mark.asyncio
async def test_taking_a_backup_applies_retention(tmp_path: Path) -> None:
    service, _ = service_for(
        tmp_path, backup_retain_daily=1, backup_retain_weekly=0, backup_retain_monthly=0
    )
    old = service.directory
    old.mkdir(mode=0o700, parents=True)
    expired = old / naming.archive_name(NOW - timedelta(days=40))
    expired.write_bytes(b"old")

    await service.create()

    assert not expired.exists()
    assert [item.name for item in service.stored()] == [naming.archive_name(NOW)]


@pytest.mark.asyncio
async def test_a_new_backup_is_verified_before_retention_removes_anything(tmp_path: Path) -> None:
    service, _ = service_for(
        tmp_path,
        tools=FakeTools(fails={RESTORE}),
        backup_retain_daily=1,
        backup_retain_weekly=0,
        backup_retain_monthly=0,
    )
    service.directory.mkdir(mode=0o700, parents=True)
    expired = service.directory / naming.archive_name(NOW - timedelta(days=40))
    expired.write_bytes(b"known older backup")

    with pytest.raises(BackupError, match="failed verification"):
        await service.create()

    assert expired.exists()
    assert [item.name for item in service.stored()] == [expired.name]


@pytest.mark.asyncio
async def test_retention_can_be_skipped_for_one_backup(tmp_path: Path) -> None:
    service, _ = service_for(
        tmp_path, backup_retain_daily=1, backup_retain_weekly=0, backup_retain_monthly=0
    )
    service.directory.mkdir(mode=0o700, parents=True)
    expired = service.directory / naming.archive_name(NOW - timedelta(days=40))
    expired.write_bytes(b"old")

    await service.create(prune=False)

    assert expired.exists()


# --- listing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_listing_reports_newest_first_and_ignores_anything_else(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path)
    service.directory.mkdir(mode=0o700, parents=True)
    for days in (3, 1, 2):
        (service.directory / naming.archive_name(NOW - timedelta(days=days))).write_bytes(b"x")
    # An operator's own files, which must never be listed, verified or deleted.
    (service.directory / "notes.txt").write_text("mine", encoding="utf-8")
    (service.directory / "context.archive.gz").write_bytes(b"not ours")

    listed = [item.name for item in service.stored()]

    assert listed == [naming.archive_name(NOW - timedelta(days=days)) for days in (1, 2, 3)]
    assert total_bytes(service.stored()) == 3


@pytest.mark.asyncio
async def test_an_empty_or_absent_directory_lists_nothing(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path)

    assert service.stored() == ()
    with pytest.raises(BackupNotFoundError):
        service.latest()
    with pytest.raises(BackupNotFoundError):
        service.get(naming.archive_name(NOW))


@pytest.mark.asyncio
async def test_a_backup_whose_manifest_is_gone_is_still_listed(tmp_path: Path) -> None:
    # Better to show it and say the manifest is unreadable than to hide a
    # backup an operator may still need.
    service, _ = service_for(tmp_path)
    stored = await service.create()
    (service.directory / naming.manifest_name(stored.name)).unlink()

    found = service.stored()

    assert [item.name for item in found] == [stored.name]
    assert found[0].manifest is None
    assert found[0].manifest_error is not None


# --- verifying -------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_untouched_backup_verifies(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path, database=fake_database())
    stored = await service.create()

    verification = await service.verify(stored)

    assert verification.sound
    assert verification.checksum_matches is True
    assert verification.readable_archive is True
    assert verification.failures == ()


@pytest.mark.asyncio
async def test_an_archive_that_changed_since_it_was_written_fails(tmp_path: Path) -> None:
    # The whole point of recording a checksum: silent corruption on disk is
    # found before the night somebody needs the backup.
    service, _ = service_for(tmp_path)
    stored = await service.create()
    stored.archive.write_bytes(ARCHIVE_BYTES + b"corruption")

    verification = await service.verify(service.get(stored.name))

    assert not verification.sound
    assert verification.checksum_matches is False
    assert "changed since it was written" in " ".join(verification.failures)


@pytest.mark.asyncio
async def test_a_manifest_for_another_database_fails_verification(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path)
    stored = await service.create()
    manifest_path = service.directory / naming.manifest_name(stored.name)
    manifest = read(manifest_path).model_copy(update={"database": "someone_else"})
    write(manifest_path, manifest)

    verification = await service.verify(service.get(stored.name))

    assert not verification.sound
    assert "different database" in " ".join(verification.failures)


@pytest.mark.asyncio
async def test_an_archive_mongorestore_cannot_read_fails(tmp_path: Path) -> None:
    service, tools = service_for(tmp_path)
    stored = await service.create()
    tools._fails = {RESTORE}

    verification = await service.verify(service.get(stored.name))

    assert not verification.sound
    assert verification.readable_archive is False


@pytest.mark.asyncio
async def test_reading_the_archive_is_a_dry_run_that_writes_nothing(tmp_path: Path) -> None:
    service, tools = service_for(tmp_path)
    stored = await service.create()

    await service.verify(service.get(stored.name))

    arguments = tools.arguments_for(RESTORE)[0]
    assert "--dryRun" in arguments
    assert "--gzip" in arguments
    assert not any(argument.startswith("--nsTo=") for argument in arguments)


@pytest.mark.asyncio
async def test_a_missing_archive_is_reported_rather_than_crashing(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path)
    stored = await service.create()
    stored.archive.unlink()

    verification = await service.verify(stored)

    assert not verification.sound
    assert verification.present is False


# --- verifying by actually restoring ---------------------------------------


@pytest.mark.asyncio
async def test_a_restore_check_never_writes_over_anything_live(tmp_path: Path) -> None:
    # It restores into this database, not another, because the application
    # authenticates as a user with readWrite on its own database and nothing
    # else — restoring elsewhere would need privileges it deliberately lacks.
    # So isolation comes from the names and from never passing --drop.
    database = fake_database({"skills": 3, "memories": 5})
    service, tools = service_for(tmp_path, database=database)
    stored = await service.create()

    await service.verify(service.get(stored.name), restore=True)

    arguments = tools.arguments_for(RESTORE)[-1]
    remap = [a for a in arguments if a.startswith("--ns")]
    assert remap == [
        "--nsFrom=agent_context_test.*",
        "--nsTo=agent_context_test.restorecheck_20260916131532_nonce__*",
    ]
    # Never --drop: an existing collection is not written over even by accident.
    assert "--drop" not in arguments
    # And the live collections are still there, untouched.
    assert {"skills", "memories"} <= set(database.counts)


@pytest.mark.asyncio
async def test_a_restore_check_removes_the_copies_it_made(tmp_path: Path) -> None:
    database = fake_database({"skills": 3, "memories": 5})
    service, _ = service_for(tmp_path, database=database)
    stored = await service.create()

    await service.verify(service.get(stored.name), restore=True)

    assert sorted(database.dropped) == [
        "restorecheck_20260916131532_nonce__memories",
        "restorecheck_20260916131532_nonce__skills",
    ]
    # Nothing restored is left behind, and nothing live was removed.
    assert set(database.counts) == {"skills", "memories"}


@pytest.mark.asyncio
async def test_a_restore_check_removes_its_copies_even_when_it_fails(tmp_path: Path) -> None:
    # A failed check must not leave a second copy of the whole database sitting
    # beside the real one, holding disk and duplicating everything it holds.
    class FailsTheScratchRestore(FakeTools):
        async def run(self, tool: str, arguments: list[str]) -> ToolResult:
            if tool == RESTORE and any(a.startswith("--nsTo=") for a in arguments):
                self.calls.append((tool, list(arguments)))
                prefix = _remap_prefix(arguments)
                if prefix is not None and self._database is not None:
                    # Half-restored, which is the state worth cleaning up.
                    self._database.restore_into(prefix)
                raise ToolFailedError("mongorestore failed: pretend")
            return await super().run(tool, arguments)

    database = fake_database()
    service, _ = service_for(
        tmp_path, tools=FailsTheScratchRestore(database=database), database=database
    )
    stored = await service.create()

    verification = await service.verify(service.get(stored.name), restore=True)

    assert not verification.sound
    assert "did not restore" in " ".join(verification.failures)
    assert database.dropped
    assert not any(name.startswith("restorecheck_") for name in database.counts)


@pytest.mark.asyncio
async def test_a_cleanup_failure_never_replaces_the_verification_result(tmp_path: Path) -> None:
    # A failure to tidy up is worth an operator's attention, but it must not
    # turn a passing check into a crash — which is exactly what it did the
    # first time this ran against a real least-privilege deployment.
    class UndroppableDatabase(FakeBackupRepository):
        async def discard_restored(self, prefix: str) -> None:
            raise OperationFailure("not authorized to drop")

    database = UndroppableDatabase({"skills": 3, "memories": 5})
    service, _ = service_for(tmp_path, database=database)
    stored = await service.create()

    verification = await service.verify(service.get(stored.name), restore=True)

    assert verification.sound
    assert verification.restored_documents == 8


@pytest.mark.asyncio
async def test_a_restore_check_compares_what_came_back_with_the_manifest(
    tmp_path: Path,
) -> None:
    # The check that makes a backup known to be restorable rather than assumed.
    database = fake_database({"skills": 3, "memories": 5})
    service, _ = service_for(tmp_path, database=database)
    stored = await service.create()

    verification = await service.verify(service.get(stored.name), restore=True)

    assert verification.sound
    assert verification.restored_documents == 8
    assert verification.expected_documents == 8


@pytest.mark.asyncio
async def test_a_restore_that_brings_back_less_than_was_taken_fails(tmp_path: Path) -> None:
    # A restore that reports success and leaves a collection short has failed,
    # and nothing in the archive itself would say so. The counts are what do.
    database = fake_database({"skills": 3, "memories": 5}, restores_as={"skills": 1})
    service, _ = service_for(tmp_path, database=database)
    stored = await service.create()

    verification = await service.verify(service.get(stored.name), restore=True)

    assert not verification.sound
    assert verification.restored_documents == 1
    assert verification.expected_documents == 8
    assert "restored 1 documents where the manifest recorded 8" in " ".join(verification.failures)


@pytest.mark.asyncio
async def test_a_restore_check_needs_a_database_and_says_so(tmp_path: Path) -> None:
    service, _ = service_for(tmp_path, database=None)
    stored = await service.create()

    verification = await service.verify(service.get(stored.name), restore=True)

    assert not verification.sound
    assert "needs a database connection" in " ".join(verification.failures)


# --- restoring -------------------------------------------------------------


@pytest.mark.asyncio
async def test_restoring_reads_the_archive_and_can_replace_collections(tmp_path: Path) -> None:
    service, tools = service_for(tmp_path)
    stored = await service.create()

    await service.restore(service.get(stored.name), drop=True)

    arguments = tools.arguments_for(RESTORE)[-1]
    assert f"--archive={stored.archive}" in arguments
    assert "--gzip" in arguments
    assert "--drop" in arguments
    assert not any(a.startswith("--nsTo=") for a in arguments)
    assert "--nsInclude=agent_context_test.*" in arguments


@pytest.mark.asyncio
async def test_restore_refuses_an_archive_that_no_longer_matches_its_manifest(
    tmp_path: Path,
) -> None:
    service, tools = service_for(tmp_path)
    stored = await service.create()
    before = len(tools.arguments_for(RESTORE))
    stored.archive.write_bytes(ARCHIVE_BYTES + b"changed")

    with pytest.raises(BackupError, match="failed verification"):
        await service.restore(service.get(stored.name), drop=True)

    new_calls = tools.arguments_for(RESTORE)[before:]
    assert new_calls
    assert all("--dryRun" in arguments for arguments in new_calls)


@pytest.mark.asyncio
async def test_restore_fails_when_live_collection_counts_do_not_match(
    tmp_path: Path,
) -> None:
    repository = fake_database({"skills": 3, "memories": 5})
    service, _ = service_for(tmp_path, database=repository)
    stored = await service.create()
    repository.counts["skills"] = 2

    with pytest.raises(BackupError, match="skills: expected 3, found 2"):
        await service.restore(stored, drop=True)


@pytest.mark.asyncio
async def test_a_failed_restore_is_reported_rather_than_swallowed(tmp_path: Path) -> None:
    service, tools = service_for(tmp_path)
    stored = await service.create()
    tools._fails = {RESTORE}

    with pytest.raises(BackupError):
        await service.restore(service.get(stored.name))


# --- expiring --------------------------------------------------------------


@pytest.mark.asyncio
async def test_pruning_removes_the_manifest_with_its_archive(tmp_path: Path) -> None:
    service, _ = service_for(
        tmp_path, backup_retain_daily=1, backup_retain_weekly=0, backup_retain_monthly=0
    )
    older, _ = service_for(
        tmp_path,
        now=NOW - timedelta(days=30),
        backup_retain_daily=1,
        backup_retain_weekly=0,
        backup_retain_monthly=0,
    )
    expired = await older.create(prune=False)
    await service.create(prune=False)

    service.prune()

    assert not expired.archive.exists()
    assert not (service.directory / naming.manifest_name(expired.name)).exists()


@pytest.mark.asyncio
async def test_a_dry_run_decides_without_deleting(tmp_path: Path) -> None:
    service, _ = service_for(
        tmp_path, backup_retain_daily=1, backup_retain_weekly=0, backup_retain_monthly=0
    )
    older, _ = service_for(
        tmp_path,
        now=NOW - timedelta(days=30),
        backup_retain_daily=1,
        backup_retain_weekly=0,
        backup_retain_monthly=0,
    )
    expired = await older.create(prune=False)
    await service.create(prune=False)

    decision = service.plan_prune()

    assert expired.name in decision.remove
    assert expired.archive.exists()


@pytest.mark.asyncio
async def test_missing_tools_are_reported_as_such(tmp_path: Path) -> None:
    class AbsentTools(FakeTools):
        async def run(self, tool: str, arguments: list[str]) -> ToolResult:
            raise ToolNotFoundError(f"{tool} was not found")

    service, _ = service_for(tmp_path, tools=AbsentTools())

    with pytest.raises(ToolNotFoundError):
        await service.create()


@pytest.mark.asyncio
async def test_a_backup_manifest_includes_every_v3_v4_and_v5_persistent_collection(
    tmp_path: Path,
) -> None:
    from app.db.client_profile_schema import CLIENT_PROFILE_COLLECTION
    from app.db.migrations.v013_research_schema import V3_RESEARCH_COLLECTIONS
    from app.db.migrations.v019_skill_update_proposals import SKILL_UPDATE_PROPOSAL_COLLECTION
    from app.db.migrations.v022_agent_identity_schema import V4_AGENT_COLLECTIONS

    persistent_collections = (
        *V3_RESEARCH_COLLECTIONS,
        SKILL_UPDATE_PROPOSAL_COLLECTION,
        *V4_AGENT_COLLECTIONS,
        CLIENT_PROFILE_COLLECTION,
    )
    counts = {name: index + 1 for index, name in enumerate(persistent_collections)}
    service, _ = service_for(tmp_path, database=fake_database(counts))

    stored = await service.create()

    manifest = read(service.directory / naming.manifest_name(stored.name))
    assert set(persistent_collections).issubset(manifest.collections)
    assert manifest.collections == dict(sorted(counts.items()))
