"""Safe database-layer errors.

These live below the repositories so both index management and query execution
can raise them without the database layer depending on repositories.
"""


class DatabaseError(RuntimeError):
    """Base class for database failures with application meaning."""


class SearchUnavailableError(DatabaseError):
    """MongoDB Search or Vector Search could not serve the request.

    Raised for coordinator, index, and query failures so callers can degrade
    without seeing driver internals.
    """
