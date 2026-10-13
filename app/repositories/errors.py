"""Safe repository-layer errors."""


class RepositoryError(RuntimeError):
    """Base class for persistence failures with application meaning."""


class ConcurrencyConflictError(RepositoryError):
    """The document was absent or changed after the caller read it."""


class ProjectIsolationError(RepositoryError):
    """An operation attempted to cross its repository's project boundary."""


class DuplicateDocumentError(RepositoryError):
    """A document with the same natural key already exists."""
