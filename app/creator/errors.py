"""Errors for creator profiles and their thematic pillars."""


class CreatorError(RuntimeError):
    """Base class for safe creator-domain failures."""


class CreatorProfileNotFoundError(CreatorError):
    """The requested profile does not exist or is unavailable to this operation."""


class CreatorProfileAlreadyExistsError(CreatorError):
    """A deterministic profile identifier has already been claimed."""


class ContentPillarNotFoundError(CreatorError):
    """The requested pillar is absent from the named creator profile."""


class ContentPillarAlreadyExistsError(CreatorError):
    """A profile already has a pillar with that identifier."""
