"""Query-vector validation shared by every vector-searchable repository."""

from collections.abc import Sequence
from math import isfinite

from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS


def validated_vector(embedding: Sequence[float]) -> list[float]:
    """Reject query vectors MongoDB could never match against an index."""
    if isinstance(embedding, str):
        raise ValueError("the query vector must be a sequence of numbers")
    values = list(embedding)
    if not 1 <= len(values) <= MAX_EMBEDDING_DIMENSIONS:
        raise ValueError(f"the query vector must hold 1 to {MAX_EMBEDDING_DIMENSIONS} values")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value):
            raise ValueError("the query vector must contain finite numbers")
    return [float(value) for value in values]
