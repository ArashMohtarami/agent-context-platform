"""Safe domain errors for the memory lifecycle."""


class MemoryError(RuntimeError):
    """Base error for the memory lifecycle."""


class MemoryNotFoundError(MemoryError):
    """Raised when a memory is absent from the requested scope.

    A memory that exists in another project is reported the same way: one
    project must not learn what another remembers, including whether an
    identifier exists at all.
    """


class MemorySupersessionError(MemoryError):
    """Raised when one memory cannot replace another.

    Superseding an already-replaced memory is refused rather than rewriting the
    link, because overwriting it would drop a correction out of the chain.
    """


class MemoryRetrievalUnavailableError(MemoryError):
    """Raised when memory retrieval is requested but not configured."""
