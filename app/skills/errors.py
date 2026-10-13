"""Safe domain errors for parsing and importing skills."""


class SkillError(RuntimeError):
    """Base error for the Phase 4 skill lifecycle."""


class SkillParseError(SkillError):
    """Raised when canonical Markdown or frontmatter is malformed."""


class SkillChunkingError(SkillError):
    """Raised when Markdown cannot be chunked without corrupting structure."""


class SkillVersionConflictError(SkillError):
    """Raised when existing immutable version content would be overwritten."""


class SkillNotFoundError(SkillError):
    """Raised when a requested skill or version is absent."""


class SkillDirectoryError(SkillError):
    """Raised when the configured canonical skill directory is unusable."""
