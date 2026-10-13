"""Query normalization, the first stage of every retrieval pipeline.

One rule governs every search: whitespace is collapsed and nothing else is
touched. Casing, colons, slashes, dots, and underscores survive, because
destroying them is what makes `wallet:read` unfindable.
"""

__all__ = ["normalize_query"]


def normalize_query(query: str) -> str:
    """Collapse whitespace without touching identifiers, casing, or symbols.

    A query of nothing but whitespace is rejected rather than normalized into
    an empty string that would match everything or nothing depending on the
    backend.
    """
    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("search query must not be empty")
    return normalized
