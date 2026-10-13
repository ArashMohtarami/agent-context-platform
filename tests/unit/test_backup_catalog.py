"""What a backup is called, and what is recorded beside it.

The name is load-bearing: retention reads the timestamp out of it, so a name
that cannot be parsed is a backup that cannot be expired, and a name parsed
wrongly is one expired at the wrong time.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.backup.manifest import BackupManifest, ManifestError, build, digest, read, write
from app.backup.naming import (
    MalformedBackupNameError,
    archive_name,
    is_backup,
    is_gzipped,
    manifest_name,
    taken_at,
)

NOW = datetime(2026, 9, 16, 13, 15, 32, tzinfo=UTC)


def manifest(**overrides: object) -> BackupManifest:
    """Build one manifest with plausible defaults."""
    values: dict[str, object] = {
        "archive": archive_name(NOW),
        "created_at": NOW,
        "database": "agent_context",
        "gzip": True,
        "bytes": 4096,
        "sha256": "a" * 64,
        "collections": {"skills": 12, "memories": 40},
    }
    values.update(overrides)
    return BackupManifest.model_validate(values)


# --- naming ----------------------------------------------------------------


def test_a_name_carries_the_instant_it_was_taken() -> None:
    name = archive_name(NOW)

    assert name == "context-20260916-131532.archive.gz"
    assert taken_at(name) == NOW


def test_the_instant_survives_a_round_trip_in_any_timezone() -> None:
    # Written in UTC whatever the host's clock is set to, so a backup taken in
    # one timezone sorts correctly beside one taken in another.
    elsewhere = NOW.astimezone(ZoneInfo("Asia/Tehran"))

    assert archive_name(elsewhere) == archive_name(NOW)
    assert taken_at(archive_name(elsewhere)) == NOW


def test_sorting_by_name_is_sorting_by_time() -> None:
    # What lets the newest backup be found without reading any file, and keeps
    # a directory listing in a useful order.
    names = [archive_name(NOW - timedelta(hours=hour)) for hour in range(30)]

    assert sorted(names, reverse=True) == names


def test_an_uncompressed_backup_says_so_in_its_name() -> None:
    # Restore has to know whether to pass --gzip, and the name is the only
    # thing available before the file is opened.
    assert is_gzipped(archive_name(NOW, gzip=True))
    assert not is_gzipped(archive_name(NOW, gzip=False))
    assert archive_name(NOW, gzip=False) == "context-20260916-131532.archive"


def test_a_manifest_sits_beside_its_archive_under_a_matching_name() -> None:
    assert manifest_name(archive_name(NOW)) == "context-20260916-131532.manifest.json"
    assert manifest_name(archive_name(NOW, gzip=False)) == "context-20260916-131532.manifest.json"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "context.archive.gz",
        "context-20260916.archive.gz",
        "context-20260916-131532.tar.gz",
        "context-20260916-131532",
        "backup-20260916-131532.archive.gz",
        "context-2026091-131532.archive.gz",
        "context-20260916-131532.archive.gz.bak",
        "CONTEXT-20260916-131532.archive.gz",
        "../context-20260916-131532.archive.gz",
        "context-20261399-251599.archive.gz",
    ],
)
def test_a_name_this_platform_did_not_write_is_not_one_of_ours(name: str) -> None:
    # What stops an operator's own files, sitting in the same directory, from
    # being listed, verified, or deleted by retention.
    assert not is_backup(name)
    with pytest.raises(MalformedBackupNameError):
        taken_at(name)


def test_every_name_this_platform_writes_is_recognised_as_its_own() -> None:
    for gzip in (True, False):
        for hours in range(0, 100, 7):
            name = archive_name(NOW - timedelta(hours=hours), gzip=gzip)
            assert is_backup(name)


# --- manifests -------------------------------------------------------------


def test_a_manifest_totals_what_the_backup_covers() -> None:
    described = manifest()

    assert described.documents == 52
    assert described.describe()["collections"] == 2


def test_a_manifest_round_trips_through_a_file(tmp_path: Path) -> None:
    path = tmp_path / "one.manifest.json"

    write(path, manifest())

    assert read(path) == manifest()


def test_a_manifest_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    # It says how much of what kind of knowledge a deployment holds, which is
    # not something every account on the host needs.
    path = tmp_path / "one.manifest.json"

    write(path, manifest())

    assert path.stat().st_mode & 0o777 == 0o600


def test_an_absent_or_damaged_manifest_says_which(tmp_path: Path) -> None:
    missing = tmp_path / "absent.manifest.json"
    corrupt = tmp_path / "corrupt.manifest.json"
    corrupt.write_text("{not json", encoding="utf-8")
    wrong = tmp_path / "wrong.manifest.json"
    wrong.write_text('{"hello": "world"}', encoding="utf-8")

    with pytest.raises(ManifestError, match="missing"):
        read(missing)
    with pytest.raises(ManifestError, match="could not be read"):
        read(corrupt)
    with pytest.raises(ManifestError, match="not a backup manifest"):
        read(wrong)


def test_a_manifest_refuses_a_digest_that_is_not_one() -> None:
    for bad in ("", "abc", "z" * 64, "A" * 64):
        with pytest.raises(ValidationError):
            manifest(sha256=bad)


def test_a_manifest_refuses_a_field_nobody_defined() -> None:
    # `extra="forbid"`: a misspelled key is a refused read rather than a value
    # that silently never applies.
    with pytest.raises(ValidationError):
        manifest(checksum="a" * 64)


def test_a_digest_is_of_the_bytes_and_notices_one_changing(tmp_path: Path) -> None:
    path = tmp_path / "archive"
    path.write_bytes(b"the backup" * 1000)
    before = digest(path)

    path.write_bytes(b"the backup" * 1000 + b"!")

    assert digest(path) != before
    assert len(before) == 64


def test_building_a_manifest_reads_the_archive_it_describes(tmp_path: Path) -> None:
    archive = tmp_path / archive_name(NOW)
    archive.write_bytes(b"pretend archive")

    built = build(
        archive=archive,
        created_at=NOW,
        database="agent_context",
        gzip=True,
        collections={"skills": 3},
    )

    assert built.archive == archive.name
    assert built.bytes == len(b"pretend archive")
    assert built.sha256 == digest(archive)
    assert built.documents == 3
