"""What was in a backup, recorded beside it.

An archive on disk answers "does a file exist". A manifest answers the
questions an operator actually has at three in the morning: is this the
database I think it is, has the file changed since it was written, and when I
restore it, how will I know it worked.

The last one is why the collection counts are here. A restore that reports
success and leaves an empty `skills` collection has failed, and nothing in the
archive itself would say so — but a count taken at backup time and compared
after restore does.

Written as a sidecar rather than inside the archive so it can be read without
decompressing gigabytes and without the MongoDB tools installed at all.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Read in blocks rather than whole: a backup is routinely larger than memory.
_DIGEST_BLOCK: Final = 1024 * 1024
# The manifest describes a backup; it never holds a credential, so this is the
# one file here that does not need 0600. It gets it anyway — the counts say how
# much of what kind of knowledge a deployment holds.
FILE_MODE: Final = 0o600


class ManifestError(ValueError):
    """The manifest is missing, unreadable, or not describing this archive."""


class BackupManifest(BaseModel):
    """A description of one backup, written when it was taken."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    archive: str
    created_at: AwareDatetime
    database: str
    gzip: bool
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # What the database held, so a restore can be checked rather than assumed.
    collections: dict[str, int] = Field(default_factory=dict)
    # Which tools wrote it, because a restore is only guaranteed compatible
    # forward, and an operator debugging one needs to know what made it.
    tool_version: str | None = None
    app_version: str | None = None
    # Commitments to random mounted keys, never their bytes, passwords or tokens.
    # All versions are required: inactive MFA factors/PKCE challenges can use old keys.
    encryption_keys: dict[str, str] = Field(default_factory=dict)

    @property
    def documents(self) -> int:
        """Return how many documents the backup covers."""
        return sum(self.collections.values())

    def describe(self) -> dict[str, object]:
        """Return a flat summary for a table or a JSON result."""
        return {
            "archive": self.archive,
            "created_at": self.created_at,
            "database": self.database,
            "bytes": self.bytes,
            "documents": self.documents,
            "collections": len(self.collections),
        }


def digest(path: Path) -> str:
    """Return the SHA-256 of a file, read in blocks."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_DIGEST_BLOCK):
            hasher.update(block)
    return hasher.hexdigest()


def write(path: Path, manifest: BackupManifest) -> None:
    """Write one manifest, readable only by its owner."""
    path.touch(mode=FILE_MODE)
    path.chmod(FILE_MODE)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def read(path: Path) -> BackupManifest:
    """Read one manifest, or say precisely why it could not be used."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"{path.name} is missing") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{path.name} could not be read") from error
    try:
        return BackupManifest.model_validate(payload)
    except ValueError as error:
        raise ManifestError(f"{path.name} is not a backup manifest") from error


def build(
    *,
    archive: Path,
    created_at: datetime,
    database: str,
    gzip: bool,
    collections: dict[str, int],
    tool_version: str | None = None,
    app_version: str | None = None,
    encryption_keys: dict[str, str] | None = None,
) -> BackupManifest:
    """Describe an archive that has just been written."""
    return BackupManifest(
        archive=archive.name,
        created_at=created_at,
        database=database,
        gzip=gzip,
        bytes=archive.stat().st_size,
        sha256=digest(archive),
        collections=collections,
        tool_version=tool_version,
        app_version=app_version,
        encryption_keys=encryption_keys or {},
    )
