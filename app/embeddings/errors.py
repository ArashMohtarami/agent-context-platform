"""Safe domain errors for local embedding generation."""


class EmbeddingError(RuntimeError):
    """Base error for every embedding failure."""


class EmbeddingConfigurationError(EmbeddingError):
    """Raised when embedding configuration is invalid or unusable on this host."""


class EmbeddingUnavailableError(EmbeddingError):
    """Raised when the local embedding backend or model cannot produce vectors."""


class EmbeddingDimensionError(EmbeddingError):
    """Raised when a vector does not match its declared embedding dimensions."""


class EmbeddingCompatibilityError(EmbeddingError):
    """Raised when a vector was produced by a different model or dimension."""


class EmbeddingInputError(EmbeddingError):
    """Raised when requested text violates the configured embedding limits."""
