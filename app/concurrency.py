"""Shared optimistic-concurrency facts safe for every transport to expose."""

from typing import Final

REVISION_CONFLICT_CODE: Final = "REVISION_CONFLICT"
REVISION_CONFLICT_MESSAGE: Final = "Resource changed since it was retrieved."
