"""Taking, checking, restoring, and expiring backups.

MongoDB is the platform's only primary state, so this is the whole of what has
to survive a lost disk. Search indexes are not backed up: they are derived, and
`contextctl skills reindex` rebuilds them from the documents. The vectors *are*
backed up, because they live in the documents and regenerating them means
running the embedding model over every chunk again — minutes to hours of CPU
for something already computed.

The policy this drives is pure and lives in `app.backup`; what is here is the
sequencing, the filesystem, and the database.
"""

import logging
import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

from app.backup import naming
from app.backup.access import require_operator
from app.backup.manifest import BackupManifest, ManifestError, build, digest, read, write
from app.backup.recovery import check_keys, required_keys
from app.backup.retention import Decision, RetentionPolicy, select
from app.backup.tools import DUMP, RESTORE, MongoTools, ToolFailedError
from app.core.config import Settings
from app.models.common import utc_now

logger = logging.getLogger("agent_context.backup")

# A backup holds every document the platform has. The directory is private and
# each file is readable only by its owner: not a defence against someone with
# the disk, but against every other account on the host.
DIRECTORY_MODE: Final = 0o700
FILE_MODE: Final = 0o600
# The prefix a restore check gives the collections it restores. No real
# collection is called this, and the check never passes --drop, so a
# verification can never overwrite what it is verifying.
CHECK_PREFIX: Final = "restorecheck"


class BackupError(RuntimeError):
    """A backup operation did not complete."""


class BackupNotFoundError(BackupError):
    """No backup by that name is in the backup directory."""


class BackupRepository(Protocol):
    """Persistence operations the backup workflow needs."""

    async def collection_counts(self) -> dict[str, int]: ...

    async def encryption_key_ids(self) -> set[str]: ...

    async def restored_document_count(self, prefix: str) -> int: ...

    async def discard_restored(self, prefix: str) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredBackup:
    """One backup on disk, and whatever is known about it."""

    archive: Path
    manifest: BackupManifest | None
    manifest_error: str | None = None

    @property
    def name(self) -> str:
        """Return the archive's filename."""
        return self.archive.name

    @property
    def created_at(self) -> datetime:
        """Return when it was taken, from its name."""
        return naming.taken_at(self.archive.name)

    @property
    def bytes(self) -> int:
        """Return its size on disk."""
        return self.archive.stat().st_size


@dataclass(frozen=True, slots=True)
class Verification:
    """What was checked about one backup, and what held."""

    name: str
    present: bool
    manifest_readable: bool
    checksum_matches: bool | None
    readable_archive: bool | None
    restored_documents: int | None = None
    expected_documents: int | None = None
    failures: tuple[str, ...] = ()

    @property
    def sound(self) -> bool:
        """Report whether every check that ran passed."""
        return not self.failures

    def describe(self) -> dict[str, object]:
        """Return a flat summary."""
        return {
            "backup": self.name,
            "sound": self.sound,
            "checksum_matches": self.checksum_matches,
            "readable_archive": self.readable_archive,
            "restored_documents": self.restored_documents,
            "expected_documents": self.expected_documents,
            "failures": list(self.failures),
        }


class BackupService:
    """Create, list, verify, restore, and expire backups."""

    def __init__(
        self,
        settings: Settings,
        tools: MongoTools,
        *,
        repository: BackupRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
        nonce: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
    ) -> None:
        self._settings = settings
        self._tools = tools
        self._repository = repository
        self._clock = clock
        self._nonce = nonce

    @property
    def directory(self) -> Path:
        """Return the configured backup directory."""
        return self._settings.backup_directory.expanduser()

    @property
    def policy(self) -> RetentionPolicy:
        """Return the configured retention policy."""
        return RetentionPolicy(
            daily=self._settings.backup_retain_daily,
            weekly=self._settings.backup_retain_weekly,
            monthly=self._settings.backup_retain_monthly,
        )

    # --- creating ----------------------------------------------------------

    async def create(self, *, gzip: bool = True, prune: bool = True) -> StoredBackup:
        """Take one backup into the configured directory, then apply retention.

        The archive is written to a temporary name and moved into place only
        once it is complete, so a backup interrupted halfway never appears in
        the directory as something a restore might later choose.
        """
        require_operator()
        created_at = self._clock()
        try:
            encryption_keys = required_keys(self._settings)
        except ValueError as error:
            raise BackupError(str(error)) from error
        if self._repository is not None and self._settings.v6_local_identity:
            required = await self._repository.encryption_key_ids()
            if required - encryption_keys.keys():
                raise BackupError(
                    "mounted keyring is missing keys referenced by encrypted identity"
                )
        target = self._prepared_directory() / naming.archive_name(created_at, gzip=gzip)
        if target.exists():
            raise BackupError(f"{target.name} already exists")
        partial = target.with_name(f".{target.name}.partial")

        collections = await self._collection_counts()
        arguments = [
            *self._tools.connection_arguments(database=self._settings.mongodb_database),
            f"--archive={partial}",
        ]
        if gzip:
            arguments.append("--gzip")
        try:
            await self._tools.run(DUMP, arguments)
            partial.chmod(FILE_MODE)
            partial.rename(target)
        except (ToolFailedError, OSError) as error:
            partial.unlink(missing_ok=True)
            raise BackupError(str(error)) from error

        try:
            manifest = build(
                archive=target,
                created_at=created_at,
                database=self._settings.mongodb_database,
                gzip=gzip,
                collections=collections,
                encryption_keys=encryption_keys,
            )
            write(self._manifest_path(target.name), manifest)
        except OSError as error:
            target.unlink(missing_ok=True)
            self._manifest_path(target.name).unlink(missing_ok=True)
            raise BackupError(f"the backup manifest could not be written: {error}") from error
        logger.info(
            "backup_created",
            extra={"backup_name": target.name, "backup_bytes": manifest.bytes},
        )
        stored = StoredBackup(archive=target, manifest=manifest)
        verification = await self.verify(stored)
        if not verification.sound:
            target.unlink(missing_ok=True)
            self._manifest_path(target.name).unlink(missing_ok=True)
            raise BackupError(
                "the new backup failed verification: " + "; ".join(verification.failures)
            )
        if prune:
            self.prune()
        return stored

    # --- listing -----------------------------------------------------------

    def stored(self) -> tuple[StoredBackup, ...]:
        """Return every backup in the directory, newest first.

        Only files this platform named are listed, so an operator's own files
        sitting beside them are never reported, verified, or deleted.
        """
        require_operator()
        directory = self.directory
        if not directory.is_dir():
            return ()
        found: list[StoredBackup] = []
        for path in sorted(directory.iterdir(), reverse=True):
            if not path.is_file() or not naming.is_backup(path.name):
                continue
            manifest: BackupManifest | None = None
            failure: str | None = None
            try:
                manifest = read(self._manifest_path(path.name))
            except ManifestError as error:
                failure = str(error)
            found.append(StoredBackup(archive=path, manifest=manifest, manifest_error=failure))
        return tuple(found)

    def latest(self) -> StoredBackup:
        """Return the newest backup, or say there is none."""
        found = self.stored()
        if not found:
            raise BackupNotFoundError(f"no backups in {self.directory}")
        return found[0]

    def get(self, name: str) -> StoredBackup:
        """Return one backup by filename."""
        for candidate in self.stored():
            if candidate.name == name:
                return candidate
        raise BackupNotFoundError(f"{name} is not in {self.directory}")

    # --- verifying ---------------------------------------------------------

    async def verify(self, backup: StoredBackup, *, restore: bool = False) -> Verification:
        """Check one backup as far as this host allows.

        Three levels, each stronger and more expensive than the last: the file
        matches the checksum recorded when it was written, `mongorestore` can
        open and plan it, and — with `restore` — it actually restores into a
        throwaway database whose document counts match the manifest.

        The last is the only one that proves a backup is restorable, which is
        why the phase asks for it and why a backup nobody has restored is not
        yet known to be a backup.
        """
        require_operator()
        failures: list[str] = []
        if not backup.archive.exists():
            return Verification(
                name=backup.name,
                present=False,
                manifest_readable=False,
                checksum_matches=None,
                readable_archive=None,
                failures=("the archive is missing",),
            )
        manifest = backup.manifest
        if manifest is None:
            failures.append(backup.manifest_error or "the manifest is unreadable")
        else:
            failures.extend(self._manifest_failures(backup, manifest))

        checksum_matches: bool | None = None
        if manifest is not None:
            checksum_matches = digest(backup.archive) == manifest.sha256
            if not checksum_matches:
                failures.append("the archive has changed since it was written")

        readable = await self._archive_parses(backup)
        if not readable:
            failures.append("mongorestore could not read the archive")

        restored: int | None = None
        expected: int | None = None
        if restore and readable and not failures:
            expected = manifest.documents if manifest is not None else None
            try:
                check_keys(self._settings, manifest.encryption_keys if manifest else {})
                restored_counts = await self._restore_into_scratch(backup)
                restored = sum(restored_counts.values())
                if manifest and manifest.collections and restored_counts != manifest.collections:
                    failures.append("isolated restore per-collection inventory does not match")
            except (BackupError, ValueError) as error:
                failures.append(str(error))
            else:
                if expected is not None and restored != expected:
                    failures.append(
                        f"restored {restored} documents where the manifest recorded {expected}"
                    )

        verification = Verification(
            name=backup.name,
            present=True,
            manifest_readable=manifest is not None,
            checksum_matches=checksum_matches,
            readable_archive=readable,
            restored_documents=restored,
            expected_documents=expected,
            failures=tuple(failures),
        )
        logger.info(
            "backup_verified",
            extra={"backup_name": backup.name, "backup_sound": verification.sound},
        )
        return verification

    async def _archive_parses(self, backup: StoredBackup) -> bool:
        """Report whether `mongorestore` can open and plan the archive.

        `--dryRun` validates restore options and archive metadata without
        writing. SHA-256 detects byte-level truncation/corruption; only an
        isolated restore proves every document can be materialized.
        """
        arguments = [
            *self._tools.connection_arguments(),
            f"--archive={backup.archive}",
            f"--nsInclude={self._settings.mongodb_database}.*",
            "--dryRun",
            "--quiet",
        ]
        if naming.is_gzipped(backup.name):
            arguments.append("--gzip")
        try:
            await self._tools.run(RESTORE, arguments)
        except ToolFailedError:
            return False
        return True

    async def _restore_into_scratch(self, backup: StoredBackup) -> dict[str, int]:
        """Restore into throwaway collections and return what arrived.

        Into *this* database under a unique prefix, not a separate one, and
        that is forced rather than chosen: the application authenticates as a
        user with `readWrite` on its own database and nothing else, so it
        cannot create or drop another. Restoring elsewhere would mean giving
        the platform cluster-wide privileges it deliberately does not have.

        Nothing live is touched. Every collection is remapped to
        `restorecheck_<timestamp>_<nonce>__<name>`, which no real collection is called,
        and `--drop` is not passed, so an existing collection is never written
        over. The copies are removed afterwards whether the check passed or not.
        """
        if self._repository is None:
            raise BackupError("a restore check needs a database connection")
        source = self._settings.mongodb_database
        prefix = f"{CHECK_PREFIX}_{self._clock().strftime('%Y%m%d%H%M%S')}_{self._nonce()}__"
        arguments = [
            *self._tools.connection_arguments(),
            f"--archive={backup.archive}",
            f"--nsFrom={source}.*",
            f"--nsTo={source}.{prefix}*",
            "--quiet",
        ]
        if naming.is_gzipped(backup.name):
            arguments.append("--gzip")
        try:
            await self._tools.run(RESTORE, arguments)
            # A matching total alone can conceal a missing identity/policy collection.
            actual = {
                name.removeprefix(prefix): count
                for name, count in (await self._repository.collection_counts()).items()
                if name.startswith(prefix)
            }
            return actual
        except ToolFailedError as error:
            raise BackupError(f"the archive did not restore: {error}") from error
        finally:
            await self._discard(prefix)

    async def _discard(self, prefix: str) -> None:
        """Remove the collections a restore check made.

        Never raises. A failure to clean up is worth an operator's attention
        but must not replace the verification result that was the point of the
        call — least of all by turning a passing check into a crash.
        """
        if self._repository is None:
            return
        try:
            await self._repository.discard_restored(prefix)
        except Exception as error:
            logger.warning(
                "backup_check_not_cleaned_up",
                extra={"backup_name": prefix, "backup_error": type(error).__name__},
            )

    # --- restoring ---------------------------------------------------------

    async def restore(self, backup: StoredBackup, *, drop: bool = False) -> None:
        """Restore one backup over the live database."""
        verification = await self.verify(backup)
        if not verification.sound:
            raise BackupError("the backup failed verification: " + "; ".join(verification.failures))
        try:
            check_keys(self._settings, backup.manifest.encryption_keys if backup.manifest else {})
        except ValueError as error:
            raise BackupError(str(error)) from error
        arguments = [
            *self._tools.connection_arguments(),
            f"--archive={backup.archive}",
            f"--nsInclude={self._settings.mongodb_database}.*",
        ]
        if naming.is_gzipped(backup.name):
            arguments.append("--gzip")
        if drop:
            arguments.append("--drop")
        try:
            await self._tools.run(RESTORE, arguments)
        except ToolFailedError as error:
            raise BackupError(str(error)) from error
        await self._verify_restored_counts(backup)
        logger.warning("backup_restored", extra={"backup_name": backup.name})

    # --- expiring ----------------------------------------------------------

    def plan_prune(self) -> Decision:
        """Decide what retention would keep and remove, without removing it."""
        return select(
            [item.name for item in self.stored()],
            policy=self.policy,
            now=self._clock(),
        )

    def prune(self) -> Decision:
        """Apply retention, deleting each expired archive and its manifest."""
        decision = self.plan_prune()
        for name in decision.remove:
            (self.directory / name).unlink(missing_ok=True)
            self._manifest_path(name).unlink(missing_ok=True)
            logger.info("backup_expired", extra={"backup_name": name})
        return decision

    # --- internals ---------------------------------------------------------

    def _prepared_directory(self) -> Path:
        """Return the backup directory, created private if it is not there."""
        directory = self.directory
        directory.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
        directory.chmod(DIRECTORY_MODE)
        return directory

    def _manifest_path(self, archive: str) -> Path:
        """Return where one archive's manifest lives."""
        return self.directory / naming.manifest_name(archive)

    async def _collection_counts(self) -> dict[str, int]:
        """Count what is about to be backed up, for the manifest.

        Returns nothing when there is no connection: the counts make a restore
        checkable, and a backup without them is still a backup.
        """
        if self._repository is None:
            return {}
        return await self._repository.collection_counts()

    def _manifest_failures(
        self,
        backup: StoredBackup,
        manifest: BackupManifest,
    ) -> list[str]:
        """Return identity and metadata mismatches for one archive sidecar."""
        failures: list[str] = []
        if manifest.archive != backup.name:
            failures.append("the manifest names a different archive")
        if manifest.database != self._settings.mongodb_database:
            failures.append("the manifest belongs to a different database")
        if manifest.gzip != naming.is_gzipped(backup.name):
            failures.append("the manifest compression does not match the archive name")
        if manifest.bytes != backup.bytes:
            failures.append("the archive size does not match the manifest")
        if manifest.created_at.replace(microsecond=0) != backup.created_at:
            failures.append("the manifest timestamp does not match the archive name")
        return failures

    async def _verify_restored_counts(self, backup: StoredBackup) -> None:
        """Compare a completed managed restore with its per-collection manifest."""
        manifest = backup.manifest
        if manifest is None or self._repository is None or not manifest.collections:
            return
        actual = await self._repository.collection_counts()
        mismatches = [
            f"{name}: expected {expected}, found {actual.get(name, 0)}"
            for name, expected in sorted(manifest.collections.items())
            if actual.get(name, 0) != expected
        ]
        if mismatches:
            raise BackupError(
                "restore completed but collection verification failed: " + "; ".join(mismatches)
            )


def disk_free(directory: Path) -> int | None:
    """Return the bytes free where backups are written, if it can be read."""
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None


def total_bytes(backups: Sequence[StoredBackup]) -> int:
    """Return how much disk the kept backups occupy."""
    return sum(item.bytes for item in backups)
