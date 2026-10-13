"""What a backup is called, and what its name tells you.

The name carries the instant it was taken, because that is the one fact every
other decision here needs: which backup is newest, which day or week or month
it belongs to, and therefore whether retention keeps it. Reading it from the
name rather than from the filesystem means a backup copied to another host,
restored from a tarball, or listed over SSH still sorts and prunes correctly —
a modification time does not survive those journeys and a name does.

Sorting lexicographically is sorting chronologically, which is why the
timestamp is fixed-width, UTC, and most-significant-first.
"""

import re
from datetime import UTC, datetime
from typing import Final

# `context-20260914-131532.archive.gz`
PREFIX: Final = "context"
ARCHIVE_SUFFIX: Final = ".archive"
GZIP_SUFFIX: Final = ".gz"
MANIFEST_SUFFIX: Final = ".manifest.json"
_STAMP: Final = "%Y%m%d-%H%M%S"
_PATTERN: Final = re.compile(
    rf"^{PREFIX}-(?P<stamp>\d{{8}}-\d{{6}})"
    rf"(?P<archive>{re.escape(ARCHIVE_SUFFIX)})(?P<gzip>{re.escape(GZIP_SUFFIX)})?$"
)


class MalformedBackupNameError(ValueError):
    """The filename is not one this platform wrote."""


def archive_name(created_at: datetime, *, gzip: bool = True) -> str:
    """Return the archive filename for a backup taken at this instant."""
    stamp = created_at.astimezone(UTC).strftime(_STAMP)
    suffix = f"{ARCHIVE_SUFFIX}{GZIP_SUFFIX}" if gzip else ARCHIVE_SUFFIX
    return f"{PREFIX}-{stamp}{suffix}"


def manifest_name(archive: str) -> str:
    """Return the manifest filename that accompanies one archive.

    A sidecar rather than a header inside the archive, so it can be read
    without decompressing gigabytes and without `mongorestore` being installed.
    """
    return f"{_stem(archive)}{MANIFEST_SUFFIX}"


def taken_at(archive: str) -> datetime:
    """Return the instant a backup was taken, from its name."""
    matched = _PATTERN.match(archive)
    if matched is None:
        raise MalformedBackupNameError(f"{archive!r} is not a backup this platform wrote")
    try:
        return datetime.strptime(matched["stamp"], _STAMP).replace(tzinfo=UTC)
    except ValueError as error:
        raise MalformedBackupNameError(
            f"{archive!r} is not a backup this platform wrote"
        ) from error


def is_backup(name: str) -> bool:
    """Report whether a filename is one of ours.

    Used to decide what a directory listing contains, so an operator's own
    files sitting beside the backups are never listed, verified, or deleted.
    """
    if _PATTERN.match(name) is None:
        return False
    try:
        taken_at(name)
    except ValueError:
        return False
    return True


def is_gzipped(archive: str) -> bool:
    """Report whether the archive name says it is compressed."""
    matched = _PATTERN.match(archive)
    if matched is None:
        raise MalformedBackupNameError(f"{archive!r} is not a backup this platform wrote")
    return matched["gzip"] is not None


def _stem(archive: str) -> str:
    """Return the name without its archive and compression suffixes."""
    matched = _PATTERN.match(archive)
    if matched is None:
        raise MalformedBackupNameError(f"{archive!r} is not a backup this platform wrote")
    return archive[: matched.start("archive")]
